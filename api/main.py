"""The read API the dashboard polls.

`frontend/src/services/apiClient.ts` has always called these five routes; until
now nothing served them, so the dashboard ran on fixtures. The paths, the field
names and the status vocabulary here are taken from
`frontend/src/types/market.ts` and must stay in step with it.

Routes:
    GET  /api/v1/signals                  StockSignal[]
    GET  /api/v1/futures                  FuturesContract[]
    GET  /api/v1/market-overview          MarketOverview
    GET  /api/v1/alerts                   raw alert rows (normalised client-side)
    POST /api/v1/alerts/{alertId}/review  record a decision; APPROVED buys in the mock account
    GET  /api/v1/orders                   mock orders, newest first
    GET  /api/v1/positions                mock cash and open positions
    POST /api/v1/positions/{ticker}/close sell a whole mock position
    POST /api/v1/telegram/webhook         Telegram button taps
    GET  /health                       unauthenticated liveness probe
"""

import hmac
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from typing import List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from config import Config
from utils import news_client
from utils.broker import get_broker
from utils.db_client import DECISIONS, DbClient
from utils.settings_store import effective_settings, settings_store
from utils.market_clock import trading_date
from utils.storage import alert_store, subscriber_store
from utils.telegram_client import (AMOUNT_PROMPT, AMOUNT_UNREADABLE, TelegramClient, build_confirm_keyboard,
                                   parse_amount_callback, parse_amount_text, parse_callback)

from . import market_source, notify, performance, trading
from .validity import format_qty, quantity_for, suggest_quantity
from .auth import auth_required, email_otp_enabled, get_otp_service, require_user

logger = logging.getLogger(__name__)

# Enriching a signal costs one Yahoo Finance round trip, so the fan-out is
# bounded. Without a cap, a long approval queue would turn one dashboard poll
# into dozens of serial lookups and time out the client's 8-second budget.
MAX_ENRICHED_SIGNALS = 20
_ENRICH_WORKERS = 8

FUTURES_UNIVERSE = [
    ("ES=F", "E-mini S&P 500", "EQUITY_INDICES"),
    ("NQ=F", "E-mini Nasdaq 100", "EQUITY_INDICES"),
    ("YM=F", "E-mini Dow", "EQUITY_INDICES"),
    ("CL=F", "Crude Oil WTI", "COMMODITIES"),
    ("GC=F", "Gold", "COMMODITIES"),
    ("SI=F", "Silver", "COMMODITIES"),
    ("BTC=F", "Bitcoin Futures", "CRYPTO"),
    ("ZN=F", "10-Year T-Note", "BONDS"),
]


# ── Request models ────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    decision: str
    # At most one; neither buys the suggested quantity. Finite and capped so no request can size an absurd order.
    shares: float | None = Field(None, gt=0, le=1_000_000, allow_inf_nan=False)
    dollars: float | None = Field(None, gt=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def one_amount_at_most(self):
        if self.shares is not None and self.dollars is not None:
            raise ValueError("send shares or dollars, not both")
        return self

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value):
        """Reject anything outside the three-word vocabulary.

        `db_client.update_alert_status` raises ValueError on an unknown status,
        which would surface as a 500. Validating at the edge turns that into a
        422 naming the accepted values.
        """
        normalised = (value or "").strip().upper()
        if normalised not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        return normalised


class SettingsIn(BaseModel):
    """Every value is range-checked here, before anything is stored."""

    pollIntervalMinutes: int = Field(ge=1, le=60)
    volumeThreshold: float = Field(ge=0.5, le=10.0)
    maxCandidatesPerRun: int = Field(ge=1, le=20)
    telegramAlertsEnabled: bool
    markets: List[Literal["us_stocks", "futures", "options"]] = Field(min_length=1)
    tradingEnabled: bool
    dollarsPerTrade: int = Field(ge=50, le=5000)
    maxOrdersPerDay: int = Field(ge=1, le=20)
    maxChasePct: float = Field(ge=0.5, le=20)
    riskPerTrade: float = Field(ge=1, le=5000)
    stopAlertPct: float = Field(ge=0.1, le=20)
    scoreAlertsEnabled: bool = False
    scoreAlertMin: int = Field(75, ge=50, le=100)
    agreementAlertsEnabled: bool = False
    requestStrategies: List[Literal["macd", "volume", "trend", "breakout", "mean_reversion", "momentum", "pullback",
                                    "accumulation", "squeeze"]]

    @field_validator("markets")
    @classmethod
    def no_duplicate_markets(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("markets must not repeat")
        return value

    @field_validator("requestStrategies")
    @classmethod
    def no_duplicate_strategies(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("requestStrategies must not repeat")
        return value


TICKER_PATTERN = r"^[A-Za-z0-9.\-]{1,10}$"


class UniverseItem(BaseModel):
    ticker: str = Field(pattern=TICKER_PATTERN)
    domain: str = Field(min_length=1, max_length=40)


class UniverseIn(BaseModel):
    items: List[UniverseItem] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def at_most_100_and_no_duplicates(cls, value):
        from utils.universe import MAX_TICKERS

        tickers = [x.ticker.upper() for x in value]
        if len(tickers) > MAX_TICKERS:
            raise ValueError(f"at most {MAX_TICKERS} tickers")
        repeated = sorted({t for t in tickers if tickers.count(t) > 1})
        if repeated:
            raise ValueError("tickers must not repeat: " + ", ".join(repeated))
        return value


# ── Dependencies ──────────────────────────────────────────────────────────────

_db_client = None


def get_db():
    """Lazily construct the DynamoDB client.

    Building it at import time would make the module unimportable without AWS
    credentials, which breaks both local tooling and test collection.
    """
    global _db_client
    if _db_client is None:
        _db_client = alert_store()
    return _db_client


_subscriber_store = None


def get_subscribers():
    """Lazily construct the subscriber store, for the same reason as get_db."""
    global _subscriber_store
    if _subscriber_store is None:
        _subscriber_store = subscriber_store()
    return _subscriber_store


# ── Assembly ──────────────────────────────────────────────────────────────────

def _plain(value):
    """DynamoDB returns numbers as Decimal; the JSON contract wants plain numbers."""
    if isinstance(value, Decimal):
        return float(value) if value % 1 else int(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _buy_price(alert_id):
    """The fill price of the alert's filled mock buy, or None (no buy, not filled, or no trades file)."""
    if not alert_id or settings_store() is None:
        return None
    store = trading.get_trades_store()
    order = store.live_buy(trading.default_account_id(store), alert_id)
    return order["fillPrice"] if order and order["status"] == "FILLED" else None


def _signal_from_alert(alert):
    """Turn a stored alert plus a live quote into a StockSignal.

    Returns None when the quote cannot be fetched, so the caller drops this row
    rather than serving a card with a zeroed price that looks like real data.
    """
    ticker = alert.get("ticker")
    if not ticker:
        return None

    snapshot = market_source.quote(ticker)
    if snapshot is None:
        return None

    analysis = alert.get("aiProsCons") or alert.get("prosAndCons") or ""
    pros, cons, summary = market_source.split_pros_cons(analysis)

    chart = snapshot["history"]
    # Tag the highest-volume bar so the chart's breakthrough marker lines up
    # with the volume spike the signal was raised for.
    if chart and (alert.get("signalType") or "VOLUME_BREAKTHROUGH") == "VOLUME_BREAKTHROUGH":
        peak = max(range(len(chart)), key=lambda i: chart[i]["volume"])
        chart[peak] = {**chart[peak], "signalTag": "Volume Breakthrough"}

    return {
        "id": alert.get("alertId"),
        "ticker": ticker,
        "companyName": snapshot["companyName"],
        "sector": snapshot["sector"],
        "industry": snapshot["industry"],
        "marketCap": snapshot["marketCap"],
        "marketCapCategory": market_source.market_cap_category(snapshot["marketCap"]),
        "currentPrice": snapshot["price"],
        "priceChange": snapshot["change"],
        "priceChangePercent": snapshot["changePercent"],
        "volume": snapshot["volume"],
        "averageVolume": snapshot["averageVolume"],
        "volumeSurgeRatio": snapshot["volumeSurgeRatio"],
        "signalType": alert.get("signalType") or "VOLUME_BREAKTHROUGH",
        "signalDescription": alert.get("signalDetails") or "",
        "strategy": alert.get("strategy"),
        "conditions": _plain(alert.get("checklist") or []),
        "entryPrice": _plain(alert.get("entryPrice")),
        "stopPrice": _plain(alert.get("stopPrice")),
        "targetPrice": _plain(alert.get("targetPrice")),
        "alertStatus": alert.get("status") or "PENDING",
        "detectedAt": alert.get("timestamp") or "",
        "aiAnalysis": {
            "bullishCatalysts": pros,
            "bearishRisks": cons,
            "summary": summary or analysis,
            "confidenceScore": _plain(alert["confidence"]) if alert.get("confidence") is not None
                else market_source._confidence(pros, cons),
            "sentiment": market_source._sentiment(pros, cons),
        },
        "chartData": chart,
        "telegramMessageId": alert.get("telegramMessageId"),
        "priceAtAlert": _plain(alert.get("priceAtAlert")),
        "buyPrice": _buy_price(alert.get("alertId")),
        "suggestedShares": suggest_quantity(trading.live_price(snapshot), _plain(alert.get("stopPrice")),
                                            effective_settings()) or None,
        "screenerUrl": f"{Config.STOCK_SCREENER_URL}/stocks/{ticker}",
    }


def _gather(items, worker):
    """Run `worker` across `items` concurrently, dropping failures.

    These are independent network reads; serially they would blow the client's
    timeout. A raising worker is logged and skipped so one bad symbol cannot
    empty the whole response.
    """
    if not items:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=_ENRICH_WORKERS) as pool:
        for outcome in pool.map(worker, items):
            if outcome is not None:
                results.append(outcome)
    return results


def _safe(worker):
    def wrapped(item):
        try:
            return worker(item)
        except Exception as e:
            logger.warning("Row dropped: %s", type(e).__name__)
            return None

    return wrapped


# ── Routes ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])


@router.get("/settings")
def get_settings():
    from utils.settings_store import ALL_MARKETS, SCANNED_MARKETS, effective_settings, settings_store

    return {
        **effective_settings(),
        "persistent": settings_store() is not None,
        "marketsAvailable": list(ALL_MARKETS),
        "marketsScanned": list(SCANNED_MARKETS),
    }


@router.put("/settings")
def put_settings(body: SettingsIn):
    from utils.settings_store import settings_store

    store = settings_store()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Settings can only be saved with the SQLite storage backend.",
        )
    gated = [s for s in body.requestStrategies if s not in ("macd", "volume")]
    if gated:  # owner, Q18: a new strategy sends buy requests only after its backtest exists
        from backtesting.store import ResearchStore

        research = ResearchStore(Config.SQLITE_PATH)
        missing = [s for s in gated if research.latest_run(s) is None]
        if missing:
            raise HTTPException(status_code=422, detail="Run the backtest for "
                                + ", ".join(STRATEGY_NAMES[s] for s in missing) + " before it can send buy requests.")
    return store.put(body.model_dump())


@router.get("/signals")
def get_signals(limit: int = Query(MAX_ENRICHED_SIGNALS, ge=1, le=MAX_ENRICHED_SIGNALS), db=Depends(get_db)):
    alerts = db.list_alerts()[:limit]
    signals = _gather(alerts, _safe(_signal_from_alert))
    news = news_client.latest_by_ticker(signal["ticker"] for signal in signals)
    dates, today = {}, date.fromisoformat(trading_date())
    if Config.STORAGE_BACKEND == "sqlite":
        from market_data.earnings import EarningsStore

        dates = EarningsStore(Config.SQLITE_PATH).get({s["ticker"].upper() for s in signals})
    from market_data.earnings import earnings_note

    for signal in signals:
        signal["news"] = news.get(signal["ticker"].upper(), [])
        day = dates.get(signal["ticker"].upper())
        signal["earningsDate"] = day.isoformat() if day else None
        signal["earningsNote"] = earnings_note(day, today)
    return signals


@router.get("/indicators/{ticker}")
def get_indicators(ticker: str = Path(..., pattern=r"^[A-Za-z0-9.\-]{1,10}$")):
    """The latest value of every indicator over the stored completed daily bars of one ticker."""
    ticker = ticker.upper()
    bars = []
    if Config.STORAGE_BACKEND == "sqlite":
        from market_data.bar_store import BarStore

        bars = BarStore(Config.SQLITE_PATH).daily([ticker], before=trading_date()).get(ticker, [])
    if not bars:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No stored daily bars for {ticker}.")
    from indicators.snapshot import latest

    return {"ticker": ticker, **latest(bars)}


@router.get("/features/{ticker}")
def get_features(ticker: str = Path(..., pattern=r"^[A-Za-z0-9.\-]{1,10}$")):
    """The ticker's features: the daily pass's snapshot when there is one, else computed from stored bars."""
    ticker = ticker.upper()
    if Config.STORAGE_BACKEND == "sqlite":
        from features.build import FeatureStore, build_features
        from features.relative_strength import SECTOR_ETF
        from market_data.bar_store import BarStore
        from strategies.history import HISTORY_BARS

        stored = FeatureStore(Config.SQLITE_PATH).latest(ticker)
        if stored:
            return {**stored, "ticker": ticker, "source": "snapshot"}
        etf = SECTOR_ETF.get(ticker)
        bars = BarStore(Config.SQLITE_PATH).daily([ticker, "SPY"] + ([etf] if etf else []), before=trading_date())
        if bars.get(ticker):
            features = build_features(bars[ticker][-HISTORY_BARS:], bars.get("SPY"), bars.get(etf) if etf else None, etf)
            return {**features, "ticker": ticker, "source": "computed"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No stored daily bars for {ticker}.")


@router.get("/regime")
def get_regime():
    """Market regime from SPY's stored completed bars; every field None when there are none."""
    from features.regime import regime

    if Config.STORAGE_BACKEND != "sqlite":
        return regime([])
    from market_data.bar_store import BarStore

    return regime(BarStore(Config.SQLITE_PATH).daily(["SPY"], before=trading_date()).get("SPY", []))


class AnalysisOut(BaseModel):
    """The standard per-ticker analysis object (DAE 16). Scores are research scores, never probabilities."""
    symbol: str
    analysis_timestamp: str | None
    timeframes: dict
    strategy_scores: dict
    overall: dict
    risk: dict
    data_quality: dict
    explanations: List[str]
    warnings: List[str]
    versions: dict


def _opportunities():
    if Config.STORAGE_BACKEND != "sqlite":
        return []
    from scoring.opportunity import OpportunityStore

    from utils.universe import universe_tickers

    return OpportunityStore(Config.SQLITE_PATH).latest(universe_tickers())


@router.get("/universe")
def get_universe():
    """The stock universe grouped by domain, in the stored order, with each ticker's exchange (or null)."""
    from utils.universe import SEED, universe_store

    store = universe_store()
    items = store.items() if store else [{"ticker": t, "domain": d, "exchange": None} for d, ts in SEED.items() for t in ts]
    return {"items": items, "persistent": store is not None}


@router.put("/universe")
def put_universe(body: UniverseIn):
    from utils.universe import universe_store

    store = universe_store()
    if store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The universe can only be saved with the SQLite storage backend.")
    items = store.replace([{"ticker": x.ticker.upper(), "domain": x.domain.strip()} for x in body.items])
    return {"items": items, "persistent": True}


@router.get("/bars/{ticker}")
def get_bars(ticker: str = Path(..., pattern=TICKER_PATTERN), days: int = Query(120, ge=5, le=500)):
    """The ticker's last `days` stored completed daily bars, oldest first."""
    ticker = ticker.upper()
    bars = []
    if Config.STORAGE_BACKEND == "sqlite":
        from market_data.bar_store import BarStore

        bars = BarStore(Config.SQLITE_PATH).daily([ticker], before=trading_date()).get(ticker, [])
    if not bars:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No stored daily bars for {ticker}.")
    return [{"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars[-days:]]


def _with_universe(rows):
    """Each opportunity with its ticker's domain and exchange from the universe (null when not listed)."""
    from utils.universe import universe_store

    store = universe_store()
    listed = {x["ticker"]: x for x in store.items()} if store else {}
    return [{**o, "domain": listed.get(o["ticker"], {}).get("domain"),
             "exchange": listed.get(o["ticker"], {}).get("exchange")} for o in rows]


@router.get("/opportunities")
def get_opportunities(
    eligible: bool | None = Query(None),
    min_score: float | None = Query(None, alias="minScore", ge=0, le=100),
    strategy: str | None = Query(None, max_length=32),
    min_risk_reward: float | None = Query(None, alias="minRiskReward", ge=0),
    data_quality: str | None = Query(None, alias="dataQuality", pattern="^(valid|warn|invalid)$"),
):
    """The latest day's opportunities, eligible first and by research score; filters narrow the list."""
    rows = _with_universe(_opportunities())
    if eligible is not None:
        rows = [o for o in rows if o["eligible"] == eligible]
    if min_score is not None:
        rows = [o for o in rows if (o["qualityScore"] or 0) >= min_score]
    if strategy:
        rows = [o for o in rows if o["strategies"].get(strategy, {}).get("direction") == "bullish"]
    if min_risk_reward is not None:
        rows = [o for o in rows if (o["risk"]["riskReward"] or 0) >= min_risk_reward]
    if data_quality:
        rows = [o for o in rows if o["dataQuality"] == data_quality]
    return [{**o, "rank": i + 1} for i, o in enumerate(rows)]


@router.get("/opportunities/{opportunity_id}/explanation")
def get_opportunity_explanation(opportunity_id: str = Path(..., max_length=40)):
    from scoring.opportunity import OpportunityStore

    o = OpportunityStore(Config.SQLITE_PATH).get(opportunity_id) if Config.STORAGE_BACKEND == "sqlite" else None
    if not o:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown opportunity.")
    return {"id": o["id"], "ticker": o["ticker"], "qualityScore": o["qualityScore"], **o["explanation"]}


def _weekly_view(tf):
    """The analysis object's "1w" entry and the alignment label, from the opportunity's timeframes (R7)."""
    if not tf:
        return {}
    weekly = tf["weekly"]
    trend = {"up": "positive", "down": "negative", "flat": "neutral"}.get(weekly["trend"])
    r = weekly["rsi14"]
    momentum = None if r is None else "positive" if r > 55 else "negative" if r < 45 else "neutral"
    return {"1w": {"trend": trend, "momentum": momentum, "score": None}, "alignment": tf["alignment"]}


@router.get("/analysis/{ticker}", response_model=AnalysisOut)
def get_analysis(ticker: str = Path(..., pattern=r"^[A-Za-z0-9.\-]{1,10}$")):
    ticker = ticker.upper()
    o = next((x for x in _opportunities() if x["ticker"] == ticker), None)
    if not o:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No analysis yet for {ticker}.")
    e = o["explanation"]
    trend_score = next((r["score"] for r in o["breakdown"] if r["component"] == "trend"), None)
    momentum_score = next((r["score"] for r in o["breakdown"] if r["component"] == "momentum"), None)
    word = lambda s: None if s is None else "positive" if s >= 60 else "negative" if s < 40 else "neutral"  # noqa: E731
    return {
        "symbol": ticker, "analysis_timestamp": o["asOf"],
        "timeframes": {"1d": {"trend": word(trend_score), "momentum": word(momentum_score), "score": o["qualityScore"]},
                       **_weekly_view(o.get("timeframes"))},
        "strategy_scores": {k: s["score"] for k, s in o["strategies"].items()},
        "overall": {"direction": o["direction"], "quality_score": o["qualityScore"], "calibrated_probability": None},
        "risk": {"risk_reward_ratio": o["risk"]["riskReward"], "risk_status": "eligible" if o["eligible"] else "blocked",
                 "plan_source": o["risk"]["planSource"]},
        "data_quality": {"status": o["dataQuality"], "coverage": o["coverage"]},
        "explanations": e["positives"] + e["negatives"], "warnings": e["warnings"] + e["gatesFailed"],
        "versions": {"feature_version": o["versions"]["features"], "strategy_versions": o["versions"]["strategies"],
                     "score_version": o["versions"]["score"]},
    }


@router.get("/health-status")
def get_health_status():
    """For the dashboard's health line: last poll, AI state, data quality. Off SQLite there is no record."""
    out = {"lastPollAt": None, "pollStale": True, "pollIntervalMinutes": effective_settings()["pollIntervalMinutes"],
           "lastAiOkAt": None, "aiFailingSince": None, "dataQuality": {"valid": 0, "warn": 0, "invalid": 0},
           "skipped": [], "newestBar": None}
    if Config.STORAGE_BACKEND != "sqlite":
        return out
    from market_data.bar_store import BarStore
    from utils.health import HealthStore

    health = HealthStore(Config.SQLITE_PATH).get_all()
    out.update({k: health.get(k) for k in ("lastPollAt", "lastAiOkAt", "aiFailingSince")})
    if out["lastPollAt"]:
        allowed = timedelta(minutes=max(15, 2 * out["pollIntervalMinutes"]))
        out["pollStale"] = datetime.now(timezone.utc) - datetime.fromisoformat(out["lastPollAt"]) > allowed
    bars = BarStore(Config.SQLITE_PATH)
    for report in bars.reports():
        out["dataQuality"][report["status"]] = out["dataQuality"].get(report["status"], 0) + 1
        if report["status"] == "invalid":
            out["skipped"].append(report["ticker"])
    out["newestBar"] = bars.latest_date()
    return out


@router.get("/futures")
def get_futures():
    def build(entry):
        return market_source.futures_contract(*entry)

    return _gather(FUTURES_UNIVERSE, _safe(build))


MARKET_TZ = ZoneInfo("America/New_York")


def _on_day(stamp, day):
    """True when an ISO timestamp (UTC if it has no offset) falls on `day` in New York."""
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(MARKET_TZ).date() == day


@router.get("/market-overview")
def get_market_overview(db=Depends(get_db)):
    alerts = db.list_alerts()
    # The trading day is New York's, so an alert at 9 p.m. ET counts as today, not tomorrow.
    today = datetime.now(MARKET_TZ).date()

    indices = {}
    for key, symbol in (("sp500", "^GSPC"), ("nasdaq", "^IXIC"), ("vix", "^VIX")):
        # A failed index read reports nulls rather than failing the whole
        # overview; the rest of the header is still worth rendering.
        indices[key] = market_source.index_quote(symbol) or {"price": None, "changePercent": None}

    return {
        "status": market_source.market_status(),
        "marketTimeUtc": datetime.now(timezone.utc).isoformat(),
        "sp500": indices["sp500"],
        "nasdaq": indices["nasdaq"],
        "vix": indices["vix"],
        "totalBreakthroughsToday": sum(1 for alert in alerts if _on_day(alert.get("timestamp"), today)),
        "pendingApprovalsCount": sum(1 for alert in alerts if alert.get("status") == "PENDING"),
    }


@router.get("/alerts")
def get_alerts(db=Depends(get_db)):
    """Return stored rows as-is.

    `services/alertContract.ts` already normalises legacy spellings and both
    timestamp formats. Re-shaping here as well would mean two places to change
    whenever the wire format moves.
    """
    return db.list_alerts()


def _worth_telling(reason):
    """A refusal the operator can act on; not a decision already made, an information alert or an expiry."""
    return reason not in (trading.ALREADY_REJECTED, trading.ALREADY_BOUGHT, trading.INFO_ONLY)         and not reason.startswith("Request expired")


@router.post("/alerts/{alert_id}/review")
def review_alert(
    background: BackgroundTasks,
    alert_id: str = Path(..., min_length=1),
    body: ReviewRequest = Body(...),
    db=Depends(get_db),
):
    existing = db.get_alert(alert_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown alert id.")
    if existing.get("status") == "EXPIRED":  # no decision may reopen or relabel an expired request
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Request expired: {existing.get('expiredReason') or 'No longer valid.'}")
    if existing.get("status") == "INFO":  # shown for information; its strategy does not send buy requests
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=trading.INFO_ONLY)

    order = None
    try:
        if body.decision == "APPROVED":
            order = trading.execute_approval(existing, db, shares=body.shares, dollars=body.dollars)
        elif body.decision == "REJECTED":
            trading.reject(existing, db)
        elif not db.update_alert_status(alert_id, body.decision):
            raise trading.DecisionNotRecorded(alert_id)
    except trading.TradeRefused as refused:
        if body.decision == "APPROVED" and _worth_telling(refused.reason):
            # A raised HTTPException drops queued background tasks, so the 409 carries its own send.
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": refused.reason},
                                background=BackgroundTask(notify.admins,
                                                          notify.refused_text(existing.get("ticker", ""), refused.reason)))
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refused.reason)
    except trading.DecisionNotRecorded:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not record the decision.",
        )
    if order is not None and order["status"] == "FILLED" and existing.get("status") != "APPROVED":
        background.add_task(notify.admins, notify.bought_text(order, existing))  # a repeat approval sends nothing

    # Mirror the decision onto the Telegram message so the two surfaces agree.
    # A failure here is logged inside the client and must not fail the request:
    # the decision is already durably recorded. A buy still in flight elsewhere
    # has not approved the alert yet, so it is not mirrored.
    if order is None or order["status"] == "FILLED":
        TelegramClient().update_message_decision(
            existing.get("telegramMessageId"), existing.get("ticker", ""), body.decision
        )

    return {"success": True, "alertId": alert_id, "status": body.decision, "order": order}


@router.get("/orders")
def get_orders():
    if Config.STORAGE_BACKEND != "sqlite":  # the trades file lives only beside the SQLite store
        return []
    store = trading.get_trades_store()
    return store.list_orders(trading.default_account_id(store))


@router.get("/data-quality")
def get_data_quality():
    """The latest data-quality report per stored ticker; empty off SQLite (no bar store there)."""
    if Config.STORAGE_BACKEND != "sqlite":
        return []
    from market_data.bar_store import BarStore

    return BarStore(Config.SQLITE_PATH).reports()


STRATEGY_NAMES = {"breakout": "Breakout", "macd": "MACD trend", "mean_reversion": "Mean reversion",
                  "trend": "Trend following", "volume": "Volume surge", "momentum": "Momentum",
                  "pullback": "Price action pullback", "accumulation": "Volume accumulation", "squeeze": "Volatility squeeze"}


def _strategy_summary(store, strategy_id):
    from backtesting.metrics import trade_stats
    from backtesting.runner import MODULES
    from scoring.reliability import strategy_reliability

    run = store.latest_run(strategy_id)
    live = store.outcomes(strategy_id)
    stats = trade_stats([{"ret": o.get("ret"), "bars_held": o.get("barsHeld")} for o in live])
    return {
        "id": strategy_id, "name": STRATEGY_NAMES[strategy_id], "version": MODULES[strategy_id].VERSION,
        "backtest": None if run is None else {k: run[k] for k in
                                              ("id", "createdAt", "strategyVersion", "backtestVersion", "params", "result")},
        "live": {**stats, "open": sum(o.get("ret") is None for o in live)},
        "reliability": strategy_reliability(store, [strategy_id])[strategy_id],
        "setups": [{k: r.get(k) for k in ("ticker", "asOf", "confidence", "newSetup", "label", "state")}
                   for r in store.latest_results(strategy_id) if r.get("direction") == "bullish"],
    }


@router.get("/strategies")
def get_strategies():
    """Each strategy with its latest stored backtest and its live alert outcomes (SQLite only)."""
    if Config.STORAGE_BACKEND != "sqlite":
        return []
    from backtesting.store import ResearchStore

    store = ResearchStore(Config.SQLITE_PATH)
    return [_strategy_summary(store, s) for s in sorted(STRATEGY_NAMES)]


@router.get("/strategies/{strategy_id}/performance")
def get_strategy_performance(strategy_id: str = Path(..., max_length=32)):
    if strategy_id not in STRATEGY_NAMES or Config.STORAGE_BACKEND != "sqlite":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown strategy.")
    from backtesting.store import ResearchStore

    store = ResearchStore(Config.SQLITE_PATH)
    summary = _strategy_summary(store, strategy_id)
    run = summary["backtest"]
    return {"strategy": summary, "trades": store.run_trades(run["id"], limit=200) if run else [],
            "outcomes": store.outcomes(strategy_id)}


@router.get("/positions")
def get_positions():
    if Config.STORAGE_BACKEND != "sqlite":
        cash = Config.MOCK_STARTING_CASH
        return {"broker": "mock", "startingCash": cash, "cash": cash, "positions": []}
    return trading.positions_snapshot()


@router.get("/performance")
def get_performance():
    # limit=-1 is SQLite for "no limit": the report needs every order, not the newest 200
    store = trading.get_trades_store() if Config.STORAGE_BACKEND == "sqlite" else None
    orders = store.list_orders(trading.default_account_id(store), limit=-1) if store else []
    alerts = get_db().list_alerts(limit=10**9)
    # Only what the report scores: filled orders and alerts with a price and date.
    needed = [o for o in orders if o["status"] == "FILLED"]
    needed += [a for a in alerts if a.get("priceAtAlert") is not None and a.get("tradingDate")]
    dates = [x["tradingDate"] for x in needed if x.get("tradingDate")]
    bars = performance.daily_bars({x["ticker"] for x in needed}, min(dates)) if dates else {}
    # Prices come only from the bars: no un-timed quote calls, so a ticker without bars has null prices.
    price_now = {t: b[-1][3] for t, b in bars.items() if b}
    return {"broker": "mock", **performance.trades_report(orders, price_now, bars),
            "recommendations": performance.recommendations_report(alerts, price_now, bars)}


@router.post("/positions/{ticker}/close")
def close_position(background: BackgroundTasks, ticker: str = Path(..., min_length=1, max_length=16)):
    if Config.STORAGE_BACKEND != "sqlite":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No open position.")
    ticker = ticker.upper()
    try:
        store = trading.get_trades_store()
        held = next((p for p in store.open_positions(trading.default_account_id(store)) if p["ticker"] == ticker), None)
        order = trading.close_position(ticker)
        if order and order.get("status") == "FILLED":
            background.add_task(notify.admins, notify.sold_text(order, held["costBasis"] if held else None))
        return order
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No open position.")
    except trading.TradeRefused as refused:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refused.reason)


# ── Paper Trading: hand-placed trades and a reset, all in the same mock account ──

class PaperBuyIn(BaseModel):
    ticker: str = Field(pattern=TICKER_PATTERN)
    # Exactly one amount; finite and capped like ReviewRequest.
    shares: float | None = Field(None, gt=0, le=1_000_000, allow_inf_nan=False)
    dollars: float | None = Field(None, gt=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def exactly_one_amount(self):
        if (self.shares is None) == (self.dollars is None):
            raise ValueError("send shares or dollars")
        return self


class PaperResetIn(BaseModel):
    startingCash: float = Field(ge=100, le=10_000_000, allow_inf_nan=False)


def _paper_store_only():
    if Config.STORAGE_BACKEND != "sqlite":  # the trades file lives only beside the SQLite store
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper trading needs the SQLite store.")


@router.get("/quote/{ticker}")
def get_quote(ticker: str = Path(..., pattern=TICKER_PATTERN)):
    """The live price a paper trade would fill at (pre/post-market when the session is closed)."""
    price = trading.price_of(ticker.upper())
    if price is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No price for that ticker.")
    return {"ticker": ticker.upper(), "price": price}


@router.post("/paper/buy")
def paper_buy(body: PaperBuyIn):
    _paper_store_only()
    try:
        return trading.manual_buy(body.ticker, shares=body.shares, dollars=body.dollars)
    except trading.TradeRefused as refused:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refused.reason)


@router.post("/paper/reset")
def paper_reset(body: PaperResetIn):
    _paper_store_only()
    try:
        return trading.reset_account(body.startingCash)
    except trading.TradeRefused as refused:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refused.reason)


# ── Email login (AUTH_MODE=email_otp) ─────────────────────────────────────────
# No require_user here: these routes are how a session is created.

class OtpRequest(BaseModel):
    email: str


class OtpVerify(BaseModel):
    email: str
    code: str


auth_router = APIRouter(prefix="/api/v1/auth")


@auth_router.post("/otp/request")
def otp_request(body: OtpRequest, background: BackgroundTasks):
    if not email_otp_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    # Same answer, at the same speed, whether or not the address is allowed: the lookup and the email run after it.
    background.add_task(get_otp_service().request_code, body.email)
    return {"ok": True}


@auth_router.post("/otp/verify")
def otp_verify(body: OtpVerify, response: Response):
    from .email_otp import COOKIE_NAME, SESSION_TTL_SECONDS

    if not email_otp_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    token = get_otp_service().verify_code(body.email, body.code)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code.")
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        secure=Config.COOKIE_SECURE, samesite="strict", path="/",
    )
    return {"ok": True}


@auth_router.get("/me")
def me(request: Request):
    """Who the session cookie belongs to. 401 when signed out, so the dashboard shows sign-in."""
    from .email_otp import COOKIE_NAME

    if not email_otp_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    from .access import is_allowed, owner_email

    email = get_otp_service().session_email(request.cookies.get(COOKIE_NAME, ""))
    if not email or not is_allowed(email):
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {"email": email, "owner": email == owner_email()}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    from .email_otp import COOKIE_NAME

    if email_otp_enabled():
        get_otp_service().end_session(request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ── Access list (P4, owner only) ──────────────────────────────────────────────

def require_owner(request: Request, user=Depends(require_user)):
    """The owner's email session; 404 without email sign-in (no list to manage), 403 for anyone else."""
    from .access import owner_email

    if not email_otp_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    if (user or {}).get("email") != owner_email():
        raise HTTPException(status_code=403, detail="Only the owner can manage access.")
    return user["email"]


class AllowedEmailIn(BaseModel):
    email: str = Field(max_length=254)


access_router = APIRouter(prefix="/api/v1/allowed-emails")


def _access_list():
    from .access import access_store, owner_email

    return {"owner": owner_email(), "emails": access_store().list()}


@access_router.get("")
def list_allowed_emails(owner: str = Depends(require_owner)):
    return _access_list()


@access_router.post("")
def add_allowed_email(body: AllowedEmailIn, owner: str = Depends(require_owner)):
    from .access import access_store, normalise, valid_email

    email = normalise(body.email)
    if not valid_email(email):
        raise HTTPException(status_code=422, detail="That is not an email address.")
    access_store().add(email, owner)
    return _access_list()


@access_router.delete("/{email}")
def remove_allowed_email(email: str = Path(..., max_length=254), owner: str = Depends(require_owner)):
    from .access import access_store, normalise, owner_email

    if normalise(email) == owner_email():
        raise HTTPException(status_code=409, detail="The owner cannot be removed.")
    access_store().remove(email)
    return _access_list()


# ── Telegram webhook ──────────────────────────────────────────────────────────
# Outside `router` because Telegram cannot present a Cognito token. It is
# authenticated instead by the shared secret configured on setWebhook.

webhook_router = APIRouter(prefix="/api/v1")


@webhook_router.post("/telegram/webhook")
def telegram_webhook(
    update: dict = Body(...),
    x_telegram_bot_api_secret_token: str = Header(default=""),
    db=Depends(get_db),
    subscribers=Depends(get_subscribers),
):
    expected = Config.TELEGRAM_WEBHOOK_SECRET
    if not expected:
        # Refuse rather than accept unauthenticated approvals.
        logger.error("TELEGRAM_WEBHOOK_SECRET is unset; refusing webhook delivery.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook not configured.")

    if not hmac.compare_digest(x_telegram_bot_api_secret_token.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook secret.")

    message = update.get("message")
    if message:
        text = (message.get("text") or "").strip()
        if text and not text.startswith("/"):
            return _handle_amount_reply(message, text, db)
        return _handle_command(message, subscribers)

    callback = update.get("callback_query")
    if not callback:
        # Telegram retries any non-2xx, so an update we do not act on is still
        # acknowledged rather than redelivered forever.
        return {"ok": True, "ignored": "not a callback_query"}

    telegram = TelegramClient()
    tapper = str((callback.get("from") or {}).get("id", ""))
    if tapper not in telegram.admin_chat_ids:
        # Subscribers get alerts without buttons; this is a guard for a stale or forwarded copy.
        telegram.answer_callback_query(callback.get("id", ""), "Only the operator can decide.")
        return {"ok": True, "ignored": "tap from non-admin"}

    data = callback.get("data", "")
    parsed = parse_callback(data)
    amount = None if parsed else parse_amount_callback(data)
    if parsed is None and amount is None:
        return {"ok": True, "ignored": "unrecognised callback data"}
    if parsed:
        alert_id, ticker, decision = parsed
        action, shares, dollars = ("buy" if decision == "APPROVED" else "reject"), None, None
    else:
        action, alert_id, shares, dollars = amount
        decision = "APPROVED"

    existing = db.get_alert(alert_id)
    if existing is None:
        telegram.answer_callback_query(callback.get("id", ""), "That alert is no longer available.")
        return {"ok": True, "ignored": "unknown alert"}
    ticker = existing.get("ticker") or (parsed[1] if parsed else "")

    cb_id = callback.get("id", "")
    tapped = callback.get("message") or {}
    chat_id = (tapped.get("chat") or {}).get("id") or tapper
    if action == "cancel":
        db.clear_prompt(chat_id)
        telegram.answer_callback_query(cb_id, "Cancelled.")
        telegram.clear_buttons(chat_id, tapped.get("message_id"))
        return {"ok": True, "alertId": alert_id, "status": existing.get("status")}
    if existing.get("status") == "EXPIRED":
        telegram.answer_callback_query(
            cb_id, f"Request expired: {existing.get('expiredReason') or 'No longer valid.'}")
        return {"ok": True, "alertId": alert_id, "status": "EXPIRED"}
    if action == "ask":
        db.set_prompt(chat_id, alert_id)
        telegram.answer_callback_query(cb_id, "")
        telegram.send_text(chat_id, AMOUNT_PROMPT)
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}

    answer = f"{ticker}: {decision.title()}"
    order = None
    try:
        if decision == "APPROVED":
            order = trading.execute_approval(existing, db, shares=shares, dollars=dollars)
            recorded = True
        else:
            recorded = trading.reject(existing, db)
    except trading.TradeRefused as refused:
        telegram.answer_callback_query(cb_id, refused.reason)
        if refused.reason in (trading.ALREADY_REJECTED, trading.ALREADY_BOUGHT, trading.INFO_ONLY):  # nothing to retry
            return {"ok": True, "alertId": alert_id, "status": existing.get("status")}
        # Buttons stay: the operator can tap again once the cause is gone.
        telegram.send_text(chat_id, f"{ticker} not bought: {refused.reason}")
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}
    except trading.DecisionNotRecorded:
        recorded = False
    except Exception:
        # A 500 makes Telegram redeliver the tap later, which would buy at a price nobody tapped against.
        logger.exception("Webhook decision %s failed for alert %s", decision, alert_id)
        if decision != "APPROVED":
            telegram.answer_callback_query(cb_id, "Could not record that decision.")
            return {"ok": True, "alertId": alert_id, "status": "PENDING"}
        telegram.answer_callback_query(cb_id, trading.BROKER_ERROR)
        telegram.send_text(chat_id, f"{ticker} not bought: {trading.BROKER_ERROR}")
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}

    if not recorded:
        telegram.answer_callback_query(cb_id, "Could not record that decision.")
    elif order is not None and order["status"] != "FILLED":
        # Another approval of this alert is mid-flight; do not claim a buy.
        telegram.answer_callback_query(cb_id, f"{ticker}: order in progress.")
    else:
        db.clear_prompt(chat_id)
        if order is not None and existing.get("status") == "APPROVED":
            answer = f"Already bought {format_qty(order['quantity'])} {ticker} (MOCK)"  # a repeat tap: one buy, one message
        elif order is not None:
            answer = f"Bought {format_qty(order['quantity'])} {ticker} (MOCK)"
            telegram.send_text(chat_id, notify.bought_text(order, existing))
        telegram.answer_callback_query(cb_id, answer)
        telegram.update_message_decision(tapped.get("message_id"), ticker, decision, chat_id=chat_id)
        stored = existing.get("telegramMessageId")
        if stored and str(stored) != str(tapped.get("message_id")):
            # A Confirm tap sits on its own message; stamp the original request too.
            telegram.update_message_decision(stored, ticker, decision, chat_id=existing.get("telegramChatId") or chat_id)

    return {"ok": True, "alertId": alert_id, "status": decision}


def _handle_amount_reply(message, text, db):
    """An admin's typed amount after "Other amount": reply with a confirm step, an error, or nothing."""
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    telegram = TelegramClient()
    if chat.get("type") != "private" or chat_id not in telegram.admin_chat_ids:
        return {"ok": True, "ignored": "text from a non-admin chat"}
    alert_id = db.get_prompt(chat_id)
    if not alert_id:
        return {"ok": True, "ignored": "no amount was asked for"}
    alert = db.get_alert(alert_id)
    if alert is None or alert.get("status") != "PENDING":
        db.clear_prompt(chat_id)
        reason = (alert or {}).get("expiredReason")
        telegram.send_text(chat_id, f"Request expired: {reason}" if reason else "That request is no longer open.")
        return {"ok": True, "ignored": "request not open"}
    amount = parse_amount_text(text)
    if amount is None:
        telegram.send_text(chat_id, AMOUNT_UNREADABLE)  # the prompt stays open
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}
    shares, dollars = amount
    price = trading.price_of(alert["ticker"])
    if price is None or price <= 0:
        telegram.send_text(chat_id, "Price unavailable.")
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}
    quantity = quantity_for(price, shares, dollars, 0)
    cost = quantity * price
    if round(cost, 2) < 1:  # same cent-precision rule as the buy itself
        telegram.send_text(chat_id, "Minimum order is $1.")
        return {"ok": True, "alertId": alert_id, "status": "PENDING"}
    db.clear_prompt(chat_id)  # the Confirm button carries the amount from here
    telegram.send_text(chat_id, f"Buy {format_qty(quantity)} {alert['ticker']}, about ${cost:.2f} (MOCK)?",
                       keyboard=build_confirm_keyboard(alert_id, shares, dollars))
    return {"ok": True, "alertId": alert_id, "status": "PENDING"}


_WELCOME = (
    "You are subscribed to trade alerts. Alerts are informational only. "
    "Send /stop to unsubscribe."
)
_GOODBYE = "You will no longer receive alerts. Send /start to subscribe again."


def _handle_command(message, subscribers):
    """Handle /start (subscribe) and /stop (unsubscribe) in a private chat."""
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return {"ok": True, "ignored": "not a private chat"}

    chat_id = str(chat.get("id", ""))
    words = (message.get("text") or "").strip().split()
    command = words[0].lower() if words else ""
    telegram = TelegramClient()

    if command == "/start":
        if subscribers.subscribe(chat_id, username=chat.get("username"), first_name=chat.get("first_name")):
            telegram.send_text(chat_id, _WELCOME)
            return {"ok": True, "subscribed": chat_id}
        telegram.send_text(chat_id, "Could not subscribe you right now. Please try again.")
        return {"ok": True, "ignored": "subscribe failed"}

    if command == "/stop":
        subscribers.deactivate(chat_id)
        telegram.send_text(chat_id, _GOODBYE)
        return {"ok": True, "unsubscribed": chat_id}

    if command in ("/pause", "/resume"):
        if chat_id not in telegram.admin_chat_ids:  # only the operator; a subscriber gets no change and no reply
            return {"ok": True, "ignored": "command from a non-admin"}
        store = settings_store()
        if store is None:
            telegram.send_text(chat_id, "Settings cannot be changed on this server.")
            return {"ok": True, "ignored": "no settings store"}
        enabled = command == "/resume"
        store.put({"tradingEnabled": enabled})
        telegram.send_text(chat_id, "Mock trading resumed." if enabled
                           else "Mock trading paused. Approvals will not buy until /resume.")
        return {"ok": True, "tradingEnabled": enabled}

    return {"ok": True, "ignored": "unknown command"}


# ── Application ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    get_broker(lambda ticker: None)  # refuses any broker other than the mock
    if Config.STORAGE_BACKEND == "sqlite":
        store = trading.get_trades_store()
        from .access import owner_email
        store.migrate_shared(owner_email() or "local-development", Config.MOCK_STARTING_CASH)  # once: shared orders go to the owner
        stranded = store.fail_stranded(0)  # cut off by the stop before this start
        if stranded:
            logger.warning("Marked %d order(s) left PENDING by the last stop as FAILED", stranded)
    # Refuse to start with email login half-configured: a missing secret or an
    # empty allow-list would leave the dashboard open or unable to sign anyone in.
    if email_otp_enabled():
        if not Config.SESSION_SECRET or len(Config.SESSION_SECRET) < 32:
            raise RuntimeError("AUTH_MODE=email_otp needs SESSION_SECRET of at least 32 characters.")
        from .access import access_store, owner_email

        if not owner_email():
            raise RuntimeError("AUTH_MODE=email_otp needs OWNER_EMAIL (the address that manages access).")
        access_store()  # creates and seeds the allowed_emails table once
    # Fail closed: with no login configured, every data route would be open to anyone who can reach the server.
    if not auth_required() and not Config.ALLOW_UNAUTHENTICATED:
        raise RuntimeError(
            "No login is configured. Set AUTH_MODE=email_otp or COGNITO_USER_POOL_ID, "
            "or ALLOW_UNAUTHENTICATED=true for local development only."
        )

    app = FastAPI(
        title="Trading Brain API",
        description="Serves breakout signals, futures, market overview and the approval queue.",
        version="1.0.0",
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # The default 422 echoes the input, and a JSON `Infinity` in it cannot be rendered (500 instead of 422).
        errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    origins = [origin.strip() for origin in Config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "authEnforced": auth_required()}

    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(access_router)
    app.include_router(webhook_router)

    return app


app = create_app()
