import logging
import uuid
from utils.api_client import ApiClient
from utils.llm_client import LlmClient
from utils.telegram_client import TelegramClient
from utils.storage import alert_store, subscriber_store
from utils.settings_store import MARKET_US_STOCKS, effective_settings
from config import Config
from api.validity import format_qty, suggest_quantity
from utils.telegram_client import build_amount_keyboard
from utils.market_clock import MARKET_TZ, session_fraction, trading_date
from datetime import datetime, timezone
from api.trading import get_trades_store, price_of
from api.validity import invalid_reason
from utils.api_client import REFERENCE, UNIVERSE
from market_data.bar_store import BarStore
from market_data.refresh import refresh_daily
from backtesting.runner import MODULES as BACKTESTED, SERIES, new_setups, run_and_store
from backtesting.store import ResearchStore
from outcomes.update import outcome_rows
from market_data.earnings import EarningsStore, download_earnings, earnings_note
from utils.health import HealthStore
from utils.llm_client import FAILED_TEXT
from features.build import FeatureStore, build_features
from features.relative_strength import SECTOR_ETF
from strategies.history import HISTORY_BARS
import time
from datetime import date
from market_data.calendar import is_trading_day, previous_trading_day, session_minutes
from strategies import macd, strategy_notes, volume
from strategies.history import completed_bars

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_macd_days = set()  # trading dates whose MACD pass finished in this process
_bar_days = set()  # trading dates whose bar refresh succeeded in this process
_backtest_days = set()  # Saturdays whose weekly backtests ran in this process
_series_days = set()  # (date, strategy) pairs whose request pass finished in this process
SERIES_TEXT = {"trend": "Trend following: new setup", "breakout": "Breakout: closing break of a 20-day high",
               "mean_reversion": "Mean reversion: first up day after a dip"}
_bar_retry_at = 0.0  # time.monotonic() before which a failed or partial refresh is not tried again
RETRY_SECONDS = 30 * 60
MIN_SESSION_MINUTES = 30  # the volume projection is too noisy before this


def _refresh_bars(today, alerts):
    """Once per trading date (SQLite only): store completed bars, re-check data quality, re-measure alert outcomes.

    A failed or partial download is retried every 30 minutes, not every poll.
    """
    global _bar_retry_at
    if Config.STORAGE_BACKEND != "sqlite" or today in _bar_days or time.monotonic() < _bar_retry_at:
        return
    store = BarStore(Config.SQLITE_PATH)
    out = refresh_daily(store, date.fromisoformat(today), UNIVERSE + REFERENCE)
    if out["failed"] or out.get("missing"):
        _bar_retry_at = time.monotonic() + RETRY_SECONDS
        if out.get("missing"):
            logger.warning("Bar refresh missing %s; retrying in 30 minutes", ", ".join(out["missing"]))
        if out["failed"]:
            return
    else:
        _bar_days.add(today)
    tickers = {a["ticker"] for a in alerts if a.get("ticker")}
    ResearchStore(Config.SQLITE_PATH).save_outcomes(outcome_rows(alerts, store.daily(tickers, before=today)))
    try:  # earnings dates ride along with the daily refresh; a failure keeps yesterday's dates
        EarningsStore(Config.SQLITE_PATH).save(download_earnings(UNIVERSE, date.fromisoformat(today)))
    except Exception:
        logger.exception("Earnings refresh failed")
    try:
        _store_features(store, today)
    except Exception:
        logger.exception("Feature snapshots failed")
    try:
        _store_setups(store, today)
    except Exception:
        logger.exception("Strategy setups failed")


def _store_features(bar_store, today):
    """After the bar refresh: one feature snapshot per universe ticker for its newest completed bar."""
    bars = bar_store.daily(UNIVERSE + REFERENCE, before=today)
    spy, features = bars.get("SPY", []), FeatureStore(Config.SQLITE_PATH)
    for ticker in UNIVERSE:
        if bars.get(ticker):
            etf = SECTOR_ETF.get(ticker)
            features.save(ticker, build_features(bars[ticker][-HISTORY_BARS:], spy, bars.get(etf) if etf else None, etf))


def _store_setups(bar_store, today):
    """After the bar refresh: every series strategy's result per universe ticker for its newest bar."""
    bars = bar_store.daily(UNIVERSE + ["SPY"], before=today)
    spy, rows = bars.get("SPY", []), []
    for module in SERIES.values():
        for ticker in UNIVERSE:
            results = module.evaluate_series(bars.get(ticker, []), spy)
            if results:
                rows.append({**results[-1], "ticker": ticker, "newSetup": len(results) - 1 in new_setups(results)})
    ResearchStore(Config.SQLITE_PATH).save_results(rows)


def _record_ai(analysis):
    """AI health for the dashboard: the time of the last real analysis, or since when it has been failing."""
    if Config.STORAGE_BACKEND != "sqlite":
        return
    health = HealthStore(Config.SQLITE_PATH)
    now = datetime.now(timezone.utc).isoformat()
    if analysis == FAILED_TEXT:
        if not health.get_all().get("aiFailingSince"):  # keep the first failure time
            health.set("aiFailingSince", now)
    else:
        health.set("lastAiOkAt", now)
        health.set("aiFailingSince", None)


def _earnings_note(ticker, today):
    if Config.STORAGE_BACKEND != "sqlite":
        return None
    return earnings_note(EarningsStore(Config.SQLITE_PATH).get([ticker]).get(ticker), date.fromisoformat(today))


def _weekly_backtests(today):
    """Saturday (US Eastern): re-run every strategy's backtest on the stored bars."""
    if Config.STORAGE_BACKEND != "sqlite" or today in _backtest_days or date.fromisoformat(today).weekday() != 5:
        return
    _backtest_days.add(today)  # once, even if a run fails: a failing run would otherwise repeat every poll
    for strategy in sorted(BACKTESTED):
        try:
            run_and_store(BarStore(Config.SQLITE_PATH), ResearchStore(Config.SQLITE_PATH), strategy, UNIVERSE, today)
        except Exception:
            logger.exception("Weekly %s backtest failed", strategy)


def _invalid_tickers(today):
    """{ticker: issues} for tickers whose stored bars cannot be trusted; they get no alert.

    Staleness is re-checked here from the stored dates, because reports are only rewritten when a
    refresh succeeds: with Yahoo down for days, the last report would still say "valid".
    """
    if Config.STORAGE_BACKEND != "sqlite":
        return {}
    store = BarStore(Config.SQLITE_PATH)
    bad = {r["ticker"]: r["issues"] for r in store.reports() if r["status"] == "invalid"}
    due = previous_trading_day(date.fromisoformat(today)).isoformat()
    for ticker in UNIVERSE:
        newest = store.newest_date(ticker)
        if newest and newest < due and ticker not in bad:  # no bars at all: a new install, history not needed for volume
            bad[ticker] = [f"Stale: newest bar {newest}, expected {due}"]
    return bad


def _request_extras(settings, alert_id, ticker, price, stop):
    """(order line, keyboard) for the admin copy: the suggested buy and the amount buttons.

    (None, None) when trading is off or there is no usable price: the message then keeps Approve/Reject.
    """
    if not settings.get("tradingEnabled") or not price:
        return None, None
    qty = suggest_quantity(price, stop, settings)
    if round(qty * price, 2) < 1:
        return "Suggested amount is under the $1 minimum (MOCK).", None
    line = f"Suggested: {format_qty(qty)} {ticker} at about ${price:.2f}, about ${qty * price:,.2f} (MOCK)."
    return line, build_amount_keyboard(alert_id, ticker, qty, settings["dollarsPerTrade"])

def _telegram_copies(alert):
    """Every recipient's copy; alerts saved before per-recipient ids fall back to the admin copy."""
    if alert.get("telegramMessages"):
        return alert["telegramMessages"]
    if alert.get("telegramMessageId") and alert.get("telegramChatId"):
        return [{"chatId": alert["telegramChatId"], "messageId": alert["telegramMessageId"],
                 "sentAt": alert.get("timestamp")}]
    return []


def expire_sweep(db, telegram, settings, price_of, alerts=None):
    """Expire every PENDING request that is no longer valid at its current price; returns how many.

    Runs at any hour. Each expired request leaves every Telegram copy and admins get a note.
    `alerts` is the poll's single alert read; expired rows are updated in place so the gates see them.
    """
    expired = 0
    for alert in alerts if alerts is not None else db.list_alerts(limit=10**9):
        if alert.get("status") != "PENDING":
            continue
        # shortcut: one Yahoo quote per open request per poll, around the clock; move to a lighter
        # price-only quote if open requests grow.
        price = price_of(alert["ticker"])  # None skips this request until the next poll
        reason = invalid_reason({k: float(alert[k]) for k in ("stopPrice", "priceAtAlert") if alert.get(k) is not None},
                                price, settings)
        if not reason or not db.expire_alert(alert["alertId"], reason):
            continue
        alert.update(status="EXPIRED", expiredReason=reason, expiredAt=datetime.now(timezone.utc).isoformat())
        expired += 1
        if not telegram.is_configured:
            continue
        note = f"{alert['ticker']} request expired: {reason}"
        cleaned = 0
        for copy in _telegram_copies(alert):
            if telegram.expire_message(copy["chatId"], int(copy["messageId"]), copy.get("sentAt"), note):
                cleaned += 1
            else:
                logger.warning("Could not clean %s copy %s in chat %s", alert["ticker"], copy["messageId"], copy["chatId"])
        # With alerts off, a note about a request nobody saw on Telegram would be noise.
        if settings["telegramAlertsEnabled"] or cleaned:
            for chat_id in sorted(telegram.admin_chat_ids):
                telegram.send_text(chat_id, note)
    return expired


def stop_watch(trades, notices, telegram, settings, price_of, today):
    """Warn admins once per ticker per day when a held mock position is within stopAlertPct of its stop.

    Information only: nothing is sold. Returns the tickers warned about.
    """
    if not settings["telegramAlertsEnabled"] or not telegram.is_configured:
        return []
    if not is_trading_day(date.fromisoformat(today)):  # prices do not move on a closed day: no repeat warnings
        return []
    sent = []
    for p in trades.open_positions():
        stop = p.get("stopPrice")
        if not stop:
            continue
        price = price_of(p["ticker"])
        if price is None or price > stop * (1 + settings["stopAlertPct"] / 100):
            continue
        if not notices.mark_notice(f"stop:{p['ticker']}:{today}"):
            continue
        word = "below" if price <= stop else "near"
        for chat_id in sorted(telegram.admin_chat_ids):
            telegram.send_text(chat_id, f"{p['ticker']} is {word} its stop ${stop:.2f}: now ${price:.2f} (MOCK position).")
        sent.append(p["ticker"])
    return sent


def _decided_today(alert, today):
    """An EXPIRED or REJECTED request decided on today's US Eastern date."""
    stamp = alert.get("expiredAt") if alert.get("status") == "EXPIRED" else alert.get("reviewedAt")
    try:
        day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(MARKET_TZ).date().isoformat()
    except (AttributeError, ValueError):
        day = alert.get("tradingDate")
    return day == today


def lambda_handler(event, context):
    """Main orchestration function for the Trading Brain."""
    api = ApiClient()
    llm = LlmClient()
    telegram = TelegramClient()
    subscribers = subscriber_store()
    db = alert_store()

    settings = effective_settings()
    if Config.STORAGE_BACKEND == "sqlite":  # the dashboard's health line reads this
        HealthStore(Config.SQLITE_PATH).set("lastPollAt", datetime.now(timezone.utc).isoformat())
    if MARKET_US_STOCKS not in settings["markets"]:
        logger.info("US stocks are not enabled in settings; skipping scan.")
        return {"statusCode": 200, "body": "US stocks disabled in settings."}

    # shortcut: on DynamoDB list_alerts is one scan page (about 1 MB), fine at a few alerts a day.
    alerts = db.list_alerts(limit=10**9)
    expire_sweep(db, telegram, settings, price_of, alerts)
    today = trading_date()
    # Each housekeeping step is isolated: its failure must not cost this poll its scan.
    try:
        _refresh_bars(today, alerts)
    except Exception:
        logger.exception("Bar refresh failed")
    _weekly_backtests(today)
    if Config.STORAGE_BACKEND == "sqlite":  # the mock account lives only on SQLite
        try:
            stop_watch(get_trades_store(), db, telegram, settings, price_of, today)
        except Exception:
            logger.exception("Stop watch failed")

    fraction = session_fraction()
    if fraction is None:
        logger.info("Market closed; skipping scan.")
        return {"statusCode": 200, "body": "Market closed; skipping scan."}
    if fraction * (session_minutes(date.fromisoformat(today)) or 390) < MIN_SESSION_MINUTES:
        logger.info("Too early in the session; skipping scan.")
        return {"statusCode": 200, "body": "Too early in the session; skipping scan."}

    logger.info("Starting market scan poll...")
    
    # 1. Fetch scan results
    scan_results = api.get_market_scan_results(session_fraction=fraction, trading_date=today)
    if not scan_results:
        logger.info("No scan results found.")
        return {"statusCode": 200, "body": "No signals identified."}

    bars = completed_bars(UNIVERSE, today)
    threshold = settings["volumeThreshold"]
    seen = {(a.get("ticker"), a.get("strategy"), a.get("tradingDate")) for a in alerts}
    # Cooldown: no new alert while one is PENDING, nor on the day one was expired or rejected.
    held = {a.get("ticker") for a in alerts if a.get("status") == "PENDING"
            or (a.get("status") in ("EXPIRED", "REJECTED") and _decided_today(a, today))}

    def raise_alert(ticker, sig, signal_desc, price):
        """Store (and, for a request strategy, send) one alert; returns whether it is a buy request."""
        requestable = sig["strategy"] in settings["requestStrategies"]
        logger.info(f"{sig['strategy']} signal for {ticker}. Running AI analysis...")

        # 3. Get detailed context for AI
        stock_data = api.get_stock_details(ticker)

        # 4. Generate Pros/Cons with LLM
        analysis = llm.analyze_stock(ticker, signal_desc, stock_data, checklist=sig["checklist"],
                                     strategy_notes=strategy_notes(sig["strategy"]))
        _record_ai(analysis)
        passed = sum(x["passed"] for x in sig["checklist"])
        conditions = f"{signal_desc}\nConditions met: {passed} of 5 ({sig['confidence']} points)"
        note = _earnings_note(ticker, today)
        if note:
            conditions += f"\n{note}"

        # 5. Send Telegram Alert
        # The id is minted before the send, not after: it rides along in the
        # buttons' callback_data, and a tap that carries no id cannot be matched
        # back to a row when the decision comes in.
        alert_id = str(uuid.uuid4())
        # Admins always get the alert (with buttons); every active subscriber gets
        # it too. Admins come first so the stored message id is the admin copy.
        message_id, sent = None, []
        if requestable and settings["telegramAlertsEnabled"]:  # dashboard-only strategies never reach Telegram
            recipients = list(dict.fromkeys(sorted(telegram.admin_chat_ids) + subscribers.active_chat_ids()))
            order_line, keyboard = _request_extras(settings, alert_id, ticker, price, sig.get("stopPrice"))
            message_id, blocked, sent = telegram.broadcast_alert(
                ticker, conditions, analysis, alert_id, recipients, order_line, keyboard
            )
            for chat_id in blocked:
                subscribers.deactivate(chat_id)
        # With Telegram off the alert is still stored, so the dashboard queue keeps it.

        # 6. Persist to DynamoDB
        db.save_alert(
            alert_id=alert_id,
            ticker=ticker,
            signal_details=signal_desc,
            pros_cons=analysis,
            chat_id=Config.TELEGRAM_CHAT_ID or next(iter(sorted(telegram.admin_chat_ids)), None),
            message_id=str(message_id) if message_id else None,
            price=price,
            strategy=sig["strategy"],
            trading_date=today,
            signal_type=sig["signalType"],
            confidence=sig["confidence"],
            checklist=sig["checklist"],
            entry=sig["entryPrice"],
            stop=sig["stopPrice"],
            target=sig["targetPrice"],
            messages=sent,
            status="PENDING" if requestable else "INFO",
        )
        seen.add((ticker, sig["strategy"], today))
        if requestable:  # an INFO alert is not an open request, so it never blocks one
            held.add(ticker)
        return requestable
    for ticker, issues in _invalid_tickers(today).items():
        logger.info("%s skipped: %s", ticker, "; ".join(issues))
        held.add(ticker)

    # 2. Process potential candidates
    signals_count = 0  # buy requests raised; MACD stops at the cap
    raised = 0  # every alert stored this poll, requests and INFO
    volume_alerts = 0  # the volume loop keeps its own cap, so INFO alerts cannot crowd out requests
    for result in scan_results:
        if volume_alerts >= settings["maxCandidatesPerRun"] or signals_count >= settings["maxCandidatesPerRun"]:
            break
        ticker = result.get("ticker")
        if not ticker: continue

        volume_score = result.get("volume_score", 0)
        if volume_score < threshold:
            continue
        # One alert per ticker per trading day; checked before any details or LLM call.
        if (ticker, "volume", today) in seen or ticker in held:
            continue
        sig = volume.evaluate(ticker, bars.get(ticker, []), result, threshold)
        signals_count += raise_alert(ticker, sig, f"Volume Breakthrough detected with score {volume_score}",
                                     result.get("price"))
        volume_alerts += 1
        raised += 1

    # MACD reads completed bars only, so one full pass per trading day is enough. A pass cut short
    # by the cap, or with no history downloaded, is retried next poll; `seen` stops repeats.
    if today not in _macd_days and bars:
        scores = {r.get("ticker"): r.get("volume_score") for r in scan_results}
        prices = {r.get("ticker"): r.get("price") for r in scan_results}
        for ticker in UNIVERSE:
            if signals_count >= settings["maxCandidatesPerRun"]:
                break
            if ticker in held:
                continue
            tb = bars.get(ticker, [])
            # A cross stays "in the last 3 bars" for 3 days: skip if an alert was raised on any of them.
            if any((ticker, "macd", d) in seen for d in [today] + [b[0] for b in tb[-2:]]):
                continue
            sig = macd.evaluate(ticker, tb, scores.get(ticker), threshold)
            if sig:
                signals_count += raise_alert(ticker, sig, "MACD crossed up above a rising 200-day average",
                                             prices.get(ticker) or sig["entryPrice"])
                raised += 1
        else:
            _macd_days.add(today)

    # Series strategies the owner turned on for buy requests: one pass per day, a request only on a new setup.
    enabled = [s for s in SERIES if s in settings["requestStrategies"] and (today, s) not in _series_days]
    if enabled and Config.STORAGE_BACKEND == "sqlite":
        history = BarStore(Config.SQLITE_PATH).daily(UNIVERSE + ["SPY"], before=today)
        prices = {r.get("ticker"): r.get("price") for r in scan_results}
        for name in enabled:
            for ticker in UNIVERSE:
                if signals_count >= settings["maxCandidatesPerRun"]:
                    break
                if ticker in held or (ticker, name, today) in seen:
                    continue
                results = SERIES[name].evaluate_series(history.get(ticker, []), history.get("SPY", []))
                if results and len(results) - 1 in new_setups(results):
                    sig = results[-1]
                    signals_count += raise_alert(ticker, sig, f"{SERIES_TEXT[name]} ({sig['confidence']} points)",
                                                 prices.get(ticker) or sig["entryPrice"])
                    raised += 1
            else:
                _series_days.add((today, name))

    logger.info(f"Completed run. Processed {raised} signals ({signals_count} buy requests).")
    return {"statusCode": 200, "body": f"Processed {raised} signals."}
