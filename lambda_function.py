import logging
import uuid
from utils.api_client import ApiClient
from utils.llm_client import LlmClient
from utils.telegram_client import TelegramClient
from utils.db_client import DbClient
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
    db = DbClient()

    logger.info("Starting market scan poll...")
    
    # 1. Fetch scan results
    scan_results = api.get_market_scan_results()
    if not scan_results:
        logger.info("No scan results found.")
        return {"statusCode": 200, "body": "No signals identified."}

    # 2. Process potential candidates
    signals_count = 0
    for result in scan_results[:5]: # Limit to top 5 candidates per run
        ticker = result.get("ticker")
        if not ticker: continue
        
        # Simple volume breakthrough check (example logic)
        volume_score = result.get("volume_score", 0)
        if volume_score < Config.STRATEGY_VOLUME_THRESHOLD:
            continue

        logger.info(f"Signal detected for {ticker}. Running AI analysis...")

        # 3. Get detailed context for AI
        stock_data = api.get_stock_details(ticker)
        
        # 4. Generate Pros/Cons with LLM
        signal_desc = f"Volume Breakthrough detected with score {volume_score}"
        analysis = llm.analyze_stock(ticker, signal_desc, stock_data)

        # 5. Send Telegram Alert
        message_id = telegram.send_alert(ticker, signal_desc, analysis)
        
        # 6. Persist to DynamoDB
        alert_id = str(uuid.uuid4())
        db.save_alert(
            alert_id=alert_id,
            ticker=ticker,
            signal_details=signal_desc,
            pros_cons=analysis,
            chat_id=Config.TELEGRAM_CHAT_ID,
            message_id=str(message_id) if message_id else None
        )
        signals_count += 1

    logger.info(f"Completed run. Processed {signals_count} signals.")
    return {"statusCode": 200, "body": f"Processed {signals_count} signals."}
