"""Runtime configuration, resolved from the environment and Secrets Manager.

`infrastructure/lambda.tf` sets `SECRET_NAME` on the function and grants it
`secretsmanager:GetSecretValue`, but nothing ever read the secret: every
sensitive value resolved to the empty string in Lambda, so the Groq client was
built with no key and the Telegram client silently had no token.

Resolution order for each value is: a non-empty environment variable, then the
corresponding key in the secret, then the default. The environment wins so a
local `.env` or a one-off override still takes effect without touching AWS, and
so the non-sensitive values `lambda.tf` sets directly are authoritative.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("SECRET_NAME", "")


def _load_secret(secret_name):
    """Fetch and parse the secret, returning {} on any failure.

    A missing or unreadable secret must not stop the process at import time:
    locally there are no AWS credentials at all, and in Lambda the environment
    still carries enough to start and log a useful error.
    """
    if not secret_name:
        return {}

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logger.warning("boto3 unavailable; skipping Secrets Manager lookup.")
        return {}

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
    except (BotoCoreError, ClientError) as e:
        logger.error("Could not read secret %s: %s", secret_name, type(e).__name__)
        return {}

    raw = response.get("SecretString")
    if not raw:
        logger.error("Secret %s has no SecretString (binary secrets are not supported).", secret_name)
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Secret %s is not valid JSON.", secret_name)
        return {}

    if not isinstance(parsed, dict):
        logger.error("Secret %s must be a JSON object of key/value pairs.", secret_name)
        return {}

    return parsed


_SECRETS = _load_secret(SECRET_NAME)


def _get(key, default=""):
    """Environment first, then the secret, then the default."""
    value = os.environ.get(key)
    if value:
        return value

    secret_value = _SECRETS.get(key)
    if secret_value:
        return str(secret_value)

    return default


def default_trades_path(sqlite_path):
    """trades.db sits next to the main database (so /data/trades.db in Docker)."""
    return os.path.join(os.path.dirname(sqlite_path), "trades.db")


class Config:
    STOCK_SCREENER_URL = _get("STOCK_SCREENER_URL", "http://localhost:8000")
    STOCK_SCREENER_API_KEY = _get("STOCK_SCREENER_API_KEY")

    GROQ_API_KEY = _get("GROQ_API_KEY")
    GROQ_MODEL = _get("GROQ_MODEL", "openai/gpt-oss-120b")

    TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")
    # Comma-separated chats that get Approve/Reject buttons. Other subscribers get
    # alerts without buttons.
    ADMIN_CHAT_IDS = _get("ADMIN_CHAT_IDS", "")
    TELEGRAM_SUBSCRIBERS_TABLE = _get("TELEGRAM_SUBSCRIBERS_TABLE", "TelegramSubscribers")
    # Shared secret echoed by Telegram in the X-Telegram-Bot-Api-Secret-Token
    # header. The webhook is a public endpoint; without this anyone who learns
    # the URL can post a forged approval.
    TELEGRAM_WEBHOOK_SECRET = _get("TELEGRAM_WEBHOOK_SECRET")

    # AInvest open API key (free, from the AInvest developer center). Unset
    # means signals are served without news.
    AINVEST_API_KEY = _get("AINVEST_API_KEY")
    NEWS_REFRESH_SECONDS = int(_get("NEWS_REFRESH_SECONDS", "180"))

    DYNAMODB_TABLE_NAME = _get("DYNAMODB_TABLE_NAME", "TradingAlerts")

    # "dynamodb" (AWS, default) or "sqlite" (single file, no AWS).
    STORAGE_BACKEND = _get("STORAGE_BACKEND", "dynamodb").lower()
    SQLITE_PATH = _get("SQLITE_PATH", "trading_brain.db")
    TRADES_SQLITE_PATH = _get("TRADES_SQLITE_PATH", default_trades_path(SQLITE_PATH))
    # Mock brokerage account (phase 2). Any BROKER other than "mock" stops create_app().
    BROKER = _get("BROKER", "mock").lower()
    MOCK_STARTING_CASH = float(_get("MOCK_STARTING_CASH", "10000"))

    # Read-API login. "email_otp" for a host without AWS; "cognito" or unset otherwise.
    AUTH_MODE = _get("AUTH_MODE", "").lower()
    # Local development only: lets the read API start with no login configured.
    ALLOW_UNAUTHENTICATED = _get("ALLOW_UNAUTHENTICATED", "").lower() == "true"
    ALLOWED_EMAILS = _get("ALLOWED_EMAILS", "")
    # Signs login codes. Required when AUTH_MODE=email_otp; generate with `openssl rand -hex 32`.
    SESSION_SECRET = _get("SESSION_SECRET", "")
    SMTP_HOST = _get("SMTP_HOST", "")
    SMTP_PORT = int(_get("SMTP_PORT", "587"))
    SMTP_USER = _get("SMTP_USER", "")
    SMTP_PASSWORD = _get("SMTP_PASSWORD", "")
    SMTP_FROM = _get("SMTP_FROM", "Trading Brain <no-reply@localhost>")
    # Local only: log login codes instead of emailing them when SMTP_HOST is unset.
    OTP_DEV_PRINT = _get("OTP_DEV_PRINT", "false").lower() == "true"
    # Session cookie Secure flag. Keep true behind HTTPS; false only for plain-http local runs.
    COOKIE_SECURE = _get("COOKIE_SECURE", "true").lower() == "true"

    # Frequency and strategy settings
    POLLING_INTERVAL_MINUTES = int(_get("POLLING_INTERVAL_MINUTES", "5"))
    STRATEGY_VOLUME_THRESHOLD = float(_get("STRATEGY_VOLUME_THRESHOLD", "3.0"))  # 300%

    # Comma-separated list of origins allowed to call the read API. The
    # dashboard is served from a different origin than the API, so the browser
    # will not send credentialed requests without this.
    CORS_ALLOWED_ORIGINS = _get("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
