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
from utils.market_clock import MARKET_TZ, MIN_SESSION_FRACTION, session_fraction, trading_date
from datetime import datetime, timezone
from api.trading import get_trades_store, price_of
from api.validity import invalid_reason
from utils.api_client import REFERENCE, UNIVERSE
from market_data.bar_store import BarStore
from market_data.refresh import refresh_daily
from backtesting.runner import MODULES as BACKTESTED, run_and_store
from backtesting.store import ResearchStore
from outcomes.update import outcome_rows
from datetime import date
from strategies import macd, strategy_notes, volume
from strategies.history import completed_bars

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_macd_days = set()  # trading dates whose MACD pass finished in this process
_bar_days = set()  # trading dates whose bar refresh succeeded in this process
_backtest_days = set()  # Saturdays whose weekly backtests ran in this process


def _refresh_bars(today, alerts):
    """Once per trading date (SQLite only): store completed bars, re-check data quality, re-measure alert outcomes."""
    if Config.STORAGE_BACKEND != "sqlite" or today in _bar_days:
        return
    store = BarStore(Config.SQLITE_PATH)
    out = refresh_daily(store, date.fromisoformat(today), UNIVERSE + REFERENCE)
    if out["failed"]:  # retried on the next poll
        return
    _bar_days.add(today)
    tickers = {a["ticker"] for a in alerts if a.get("ticker")}
    ResearchStore(Config.SQLITE_PATH).save_outcomes(outcome_rows(alerts, store.daily(tickers, before=today)))


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


def _invalid_tickers():
    """{ticker: issues} for tickers whose stored bars cannot be trusted; they get no alert."""
    if Config.STORAGE_BACKEND != "sqlite":
        return {}
    return {r["ticker"]: r["issues"] for r in BarStore(Config.SQLITE_PATH).reports() if r["status"] == "invalid"}


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
    if MARKET_US_STOCKS not in settings["markets"]:
        logger.info("US stocks are not enabled in settings; skipping scan.")
        return {"statusCode": 200, "body": "US stocks disabled in settings."}

    # shortcut: on DynamoDB list_alerts is one scan page (about 1 MB), fine at a few alerts a day.
    alerts = db.list_alerts(limit=10**9)
    expire_sweep(db, telegram, settings, price_of, alerts)
    _refresh_bars(trading_date(), alerts)
    _weekly_backtests(trading_date())
    if Config.STORAGE_BACKEND == "sqlite":  # the mock account lives only on SQLite
        stop_watch(get_trades_store(), db, telegram, settings, price_of, trading_date())

    fraction = session_fraction()
    if fraction is None:
        logger.info("Market closed; skipping scan.")
        return {"statusCode": 200, "body": "Market closed; skipping scan."}
    if fraction < MIN_SESSION_FRACTION:
        logger.info("Too early in the session; skipping scan.")
        return {"statusCode": 200, "body": "Too early in the session; skipping scan."}
    today = trading_date()

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
        logger.info(f"{sig['strategy']} signal for {ticker}. Running AI analysis...")

        # 3. Get detailed context for AI
        stock_data = api.get_stock_details(ticker)

        # 4. Generate Pros/Cons with LLM
        analysis = llm.analyze_stock(ticker, signal_desc, stock_data, checklist=sig["checklist"],
                                     strategy_notes=strategy_notes(sig["strategy"]))
        passed = sum(x["passed"] for x in sig["checklist"])
        conditions = f"{signal_desc}\nConditions met: {passed} of 5 ({sig['confidence']} points)"

        # 5. Send Telegram Alert
        # The id is minted before the send, not after: it rides along in the
        # buttons' callback_data, and a tap that carries no id cannot be matched
        # back to a row when the decision comes in.
        alert_id = str(uuid.uuid4())
        # Admins always get the alert (with buttons); every active subscriber gets
        # it too. Admins come first so the stored message id is the admin copy.
        message_id, sent = None, []
        if settings["telegramAlertsEnabled"]:
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
        )
        seen.add((ticker, sig["strategy"], today))
        held.add(ticker)
    for ticker, issues in _invalid_tickers().items():
        logger.info("%s skipped: %s", ticker, "; ".join(issues))
        held.add(ticker)

    # 2. Process potential candidates
    signals_count = 0
    for result in scan_results:
        if signals_count >= settings["maxCandidatesPerRun"]:
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
        raise_alert(ticker, sig, f"Volume Breakthrough detected with score {volume_score}", result.get("price"))
        signals_count += 1

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
                raise_alert(ticker, sig, "MACD crossed up above a rising 200-day average",
                            prices.get(ticker) or sig["entryPrice"])
                signals_count += 1
        else:
            _macd_days.add(today)

    logger.info(f"Completed run. Processed {signals_count} signals.")
    return {"statusCode": 200, "body": f"Processed {signals_count} signals."}
