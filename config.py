import os

class Config:
    STOCK_SCREENER_URL = os.environ.get("STOCK_SCREENER_URL", "http://localhost:8000")
    STOCK_SCREENER_API_KEY = os.environ.get("STOCK_SCREENER_API_KEY", "")
    
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "TradingAlerts")
    
    # Frequency and strategy settings
    POLLING_INTERVAL_MINUTES = int(os.environ.get("POLLING_INTERVAL_MINUTES", 15))
    STRATEGY_VOLUME_THRESHOLD = float(os.environ.get("STRATEGY_VOLUME_THRESHOLD", 3.0)) # 300%

    # Feature flags
    FEATURE_MULTI_AGENT_MODE = os.environ.get("FEATURE_MULTI_AGENT_MODE", "false").lower() == "true"
    FEATURE_UI_DASHBOARD = os.environ.get("FEATURE_UI_DASHBOARD", "false").lower() == "true"
