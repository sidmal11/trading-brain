import logging
import uuid
from utils.api_client import ApiClient
from utils.llm_client import LlmClient
from utils.telegram_client import TelegramClient
from utils.storage import alert_store, subscriber_store
from utils.settings_store import MARKET_US_STOCKS, effective_settings
from config import Config
from utils.broker import whole_shares
from graph import run_workflow
from utils.langgraph_state import TradingBrainState
from utils.market_clock import MIN_SESSION_FRACTION, session_fraction, trading_date
from utils.api_client import UNIVERSE
from strategies import macd, strategy_notes, volume
from strategies.history import completed_bars

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_macd_days = set()  # trading dates whose MACD pass finished in this process


def _order_line(settings, ticker, price):
    """What Approve would do, shown on the admin copy only."""
    if not settings.get("tradingEnabled") or not price:
        return None
    dollars = settings["dollarsPerTrade"]
    q = whole_shares(dollars, price)
    if q == 0:
        return f"Approve will not buy: ${dollars} is below one share at ${price:.2f} (MOCK)."
    return f"Approve buys {q} {ticker} at about ${price:.2f}, about ${q * price:,.0f} (MOCK)."

def lambda_handler(event, context):
    """Main orchestration function for the Trading Brain."""
    if Config.FEATURE_MULTI_AGENT_MODE:
        run_workflow(TradingBrainState())
        logger.info("Multi-agent workflow completed.")
        return {"statusCode": 200, "body": "Multi-agent workflow completed."}

    api = ApiClient()
    llm = LlmClient()
    telegram = TelegramClient()
    subscribers = subscriber_store()
    db = alert_store()

    settings = effective_settings()
    if MARKET_US_STOCKS not in settings["markets"]:
        logger.info("US stocks are not enabled in settings; skipping scan.")
        return {"statusCode": 200, "body": "US stocks disabled in settings."}

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
    # shortcut: on DynamoDB list_alerts is one scan page (about 1 MB), fine at a few alerts a day.
    seen = {(a.get("ticker"), a.get("strategy"), a.get("tradingDate")) for a in db.list_alerts(limit=10**9)}

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
        message_id = None
        if settings["telegramAlertsEnabled"]:
            recipients = list(dict.fromkeys(sorted(telegram.admin_chat_ids) + subscribers.active_chat_ids()))
            message_id, blocked = telegram.broadcast_alert(
                ticker, conditions, analysis, alert_id, recipients, _order_line(settings, ticker, price)
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
        )
        seen.add((ticker, sig["strategy"], today))

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
        if (ticker, "volume", today) in seen:
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
