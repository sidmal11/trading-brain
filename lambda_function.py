import logging
import uuid
from utils.api_client import ApiClient
from utils.llm_client import LlmClient
from utils.telegram_client import TelegramClient
from utils.storage import alert_store, subscriber_store
from utils.settings_store import MARKET_US_STOCKS, effective_settings
from config import Config
from graph import run_workflow
from utils.langgraph_state import TradingBrainState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

    logger.info("Starting market scan poll...")
    
    # 1. Fetch scan results
    scan_results = api.get_market_scan_results()
    if not scan_results:
        logger.info("No scan results found.")
        return {"statusCode": 200, "body": "No signals identified."}

    # 2. Process potential candidates
    signals_count = 0
    for result in scan_results[:settings["maxCandidatesPerRun"]]:
        ticker = result.get("ticker")
        if not ticker: continue
        
        # Simple volume breakthrough check (example logic)
        volume_score = result.get("volume_score", 0)
        if volume_score < settings["volumeThreshold"]:
            continue

        logger.info(f"Signal detected for {ticker}. Running AI analysis...")

        # 3. Get detailed context for AI
        stock_data = api.get_stock_details(ticker)
        
        # 4. Generate Pros/Cons with LLM
        signal_desc = f"Volume Breakthrough detected with score {volume_score}"
        analysis = llm.analyze_stock(ticker, signal_desc, stock_data)

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
            message_id, blocked = telegram.broadcast_alert(ticker, signal_desc, analysis, alert_id, recipients)
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
            message_id=str(message_id) if message_id else None
        )
        signals_count += 1

    logger.info(f"Completed run. Processed {signals_count} signals.")
    return {"statusCode": 200, "body": f"Processed {signals_count} signals."}
