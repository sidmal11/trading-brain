"""The read API the dashboard polls.

`frontend/src/services/apiClient.ts` has always called these five routes; until
now nothing served them, so the dashboard ran on fixtures. The paths, the field
names and the status vocabulary here are taken from
`frontend/src/types/market.ts` and must stay in step with it.

Routes:
    GET  /api/signals                  StockSignal[]
    GET  /api/futures                  FuturesContract[]
    GET  /api/market-overview          MarketOverview
    GET  /api/alerts                   raw alert rows (normalised client-side)
    POST /api/alerts/{alertId}/review  record an approve/reject decision
    POST /api/telegram/webhook         Telegram button taps
    GET  /health                       unauthenticated liveness probe
"""

import hmac
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

from config import Config
from utils import news_client
from utils.broker import get_broker
from utils.db_client import VALID_STATUSES, DbClient
from utils.storage import alert_store, subscriber_store
from utils.telegram_client import TelegramClient, parse_callback

from . import market_source, top_stocks
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

    @field_validator("decision")
    @classmethod
    def decision_must_be_known(cls, value):
        """Reject anything outside the three-word vocabulary.

        `db_client.update_alert_status` raises ValueError on an unknown status,
        which would surface as a 500. Validating at the edge turns that into a
        422 naming the accepted values.
        """
        normalised = (value or "").strip().upper()
        if normalised not in VALID_STATUSES:
            raise ValueError(f"decision must be one of {sorted(VALID_STATUSES)}")
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

    @field_validator("markets")
    @classmethod
    def no_duplicate_markets(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("markets must not repeat")
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
    if chart:
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
        # The screener's relative-strength rank is not stored on the alert, so
        # it is not invented here. 0 reads as "unranked" in the UI's 1-99 scale.
        "relativeStrengthRank": int(alert.get("relativeStrengthRank") or 0),
        "signalType": alert.get("signalType") or "VOLUME_BREAKTHROUGH",
        "signalDescription": alert.get("signalDetails") or "",
        "alertStatus": alert.get("status") or "PENDING",
        "detectedAt": alert.get("timestamp") or "",
        "aiAnalysis": {
            "bullishCatalysts": pros,
            "bearishRisks": cons,
            "summary": summary or analysis,
            "confidenceScore": market_source._confidence(pros, cons),
            "sentiment": market_source._sentiment(pros, cons),
        },
        "chartData": chart,
        "telegramMessageId": alert.get("telegramMessageId"),
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

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


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
    return store.put(body.model_dump())


@router.get("/signals")
def get_signals(limit: int = Query(MAX_ENRICHED_SIGNALS, ge=1, le=MAX_ENRICHED_SIGNALS), db=Depends(get_db)):
    alerts = db.list_alerts()[:limit]
    signals = _gather(alerts, _safe(_signal_from_alert))
    news = news_client.latest_by_ticker(signal["ticker"] for signal in signals)
    for signal in signals:
        signal["news"] = news.get(signal["ticker"].upper(), [])
    return signals


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
        # A failed index read reports zeroes rather than failing the whole
        # overview; the rest of the header is still worth rendering.
        indices[key] = market_source.index_quote(symbol) or {"price": 0.0, "changePercent": 0.0}

    return {
        "status": market_source.market_status(),
        "marketTimeUtc": datetime.now(timezone.utc).isoformat(),
        "sp500": indices["sp500"],
        "nasdaq": indices["nasdaq"],
        "vix": indices["vix"],
        "totalBreakthroughsToday": sum(1 for alert in alerts if _on_day(alert.get("timestamp"), today)),
        "pendingApprovalsCount": sum(1 for alert in alerts if alert.get("status") == "PENDING"),
    }


@router.get("/top-stocks")
def get_top_stocks():
    return top_stocks.top_stock_groups()


@router.get("/alerts")
def get_alerts(db=Depends(get_db)):
    """Return stored rows as-is.

    `services/alertContract.ts` already normalises legacy spellings and both
    timestamp formats. Re-shaping here as well would mean two places to change
    whenever the wire format moves.
    """
    return db.list_alerts()


@router.post("/alerts/{alert_id}/review")
def review_alert(
    alert_id: str = Path(..., min_length=1),
    body: ReviewRequest = Body(...),
    db=Depends(get_db),
):
    existing = db.get_alert(alert_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown alert id.")

    if not db.update_alert_status(alert_id, body.decision):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not record the decision.",
        )

    # Mirror the decision onto the Telegram message so the two surfaces agree.
    # A failure here is logged inside the client and must not fail the request:
    # the decision is already durably recorded.
    TelegramClient().update_message_decision(
        existing.get("telegramMessageId"), existing.get("ticker", ""), body.decision
    )

    return {"success": True, "alertId": alert_id, "status": body.decision}


# ── Email login (AUTH_MODE=email_otp) ─────────────────────────────────────────
# No require_user here: these routes are how a session is created.

class OtpRequest(BaseModel):
    email: str


class OtpVerify(BaseModel):
    email: str
    code: str


auth_router = APIRouter(prefix="/api/auth")


@auth_router.post("/otp/request")
def otp_request(body: OtpRequest):
    if not email_otp_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    get_otp_service().request_code(body.email)
    # Same answer whether or not the address is allowed.
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
    email = get_otp_service().session_email(request.cookies.get(COOKIE_NAME, ""))
    if not email:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {"email": email}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    from .email_otp import COOKIE_NAME

    if email_otp_enabled():
        get_otp_service().end_session(request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ── Telegram webhook ──────────────────────────────────────────────────────────
# Outside `router` because Telegram cannot present a Cognito token. It is
# authenticated instead by the shared secret configured on setWebhook.

webhook_router = APIRouter(prefix="/api")


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

    parsed = parse_callback(callback.get("data", ""))
    if parsed is None:
        return {"ok": True, "ignored": "unrecognised callback data"}

    alert_id, ticker, decision = parsed

    if db.get_alert(alert_id) is None:
        telegram.answer_callback_query(callback.get("id", ""), "That alert is no longer available.")
        return {"ok": True, "ignored": "unknown alert"}

    if db.update_alert_status(alert_id, decision):
        telegram.answer_callback_query(callback.get("id", ""), f"{ticker}: {decision.title()}")
        tapped = callback.get("message") or {}
        telegram.update_message_decision(
            tapped.get("message_id"), ticker, decision, chat_id=(tapped.get("chat") or {}).get("id")
        )
    else:
        telegram.answer_callback_query(callback.get("id", ""), "Could not record that decision.")

    return {"ok": True, "alertId": alert_id, "status": decision}


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

    return {"ok": True, "ignored": "unknown command"}


# ── Application ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    get_broker(lambda ticker: None)  # refuses any broker other than the mock
    # Refuse to start with email login half-configured: a missing secret or an
    # empty allow-list would leave the dashboard open or unable to sign anyone in.
    if email_otp_enabled():
        if not Config.SESSION_SECRET or len(Config.SESSION_SECRET) < 32:
            raise RuntimeError("AUTH_MODE=email_otp needs SESSION_SECRET of at least 32 characters.")
        if not Config.ALLOWED_EMAILS.strip():
            raise RuntimeError("AUTH_MODE=email_otp needs ALLOWED_EMAILS.")
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

    origins = [origin.strip() for origin in Config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "authEnforced": auth_required()}

    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(webhook_router)

    return app


app = create_app()
