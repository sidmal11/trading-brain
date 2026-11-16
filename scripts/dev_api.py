"""Run the read API locally with an in-memory alert store.

Development only. The real API reads DynamoDB; this swaps that one dependency
for a dict so the dashboard can be driven end to end without AWS credentials, a
provisioned table, or a deployed Lambda. Market data is still fetched live from
Yahoo Finance, so prices and charts are real.

    python scripts/dev_api.py

Then point the frontend at it and leave demo mode off:

    VITE_TRADING_BRAIN_API_URL=http://localhost:8000
    VITE_USE_MOCK_DATA=false

Auth is not enforced unless COGNITO_USER_POOL_ID is set, and the app logs a
warning saying so on startup.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

# Before the api import: Config reads the environment at import time.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import uvicorn  # noqa: E402

from api import main as api_main  # noqa: E402
from utils.db_client import VALID_STATUSES  # noqa: E402


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


SEED = [
    {
        "alertId": str(uuid.uuid4()),
        "ticker": "NVDA",
        "timestamp": _ago(25),
        "signalDetails": "Volume Breakthrough detected with score 3.41",
        "aiProsCons": (
            "Accumulation above prior resistance on heavy volume.\n"
            "Pros\n"
            "- Data-centre bookings ahead of guidance\n"
            "- Breakout confirmed on 2x average volume\n"
            "Cons\n"
            "- Valuation leaves little room for a miss\n"
            "- Concentrated customer base\n"
        ),
        "status": "PENDING",
        "telegramMessageId": "9850",
    },
    {
        "alertId": str(uuid.uuid4()),
        "ticker": "PLTR",
        "timestamp": _ago(70),
        "signalDetails": "Volume Breakthrough detected with score 3.02",
        "aiProsCons": (
            "Pros\n"
            "- Commercial customer count expanding\n"
            "- Clean break over a prior high\n"
            "Cons\n"
            "- Momentum crowding raises reversal risk\n"
        ),
        "status": "PENDING",
        "telegramMessageId": "9851",
    },
    {
        "alertId": str(uuid.uuid4()),
        "ticker": "AMD",
        "timestamp": _ago(180),
        "signalDetails": "Consolidation breakout above range high",
        "aiProsCons": "Pros\n- Share gains in server CPUs\nCons\n- Soft client segment\n",
        "status": "APPROVED",
        "reviewedAt": _ago(150),
        "telegramMessageId": "9852",
    },
]


class InMemoryDb:
    """The slice of DbClient the API actually calls."""

    def __init__(self, rows):
        self.rows = {row["alertId"]: dict(row) for row in rows}

    def list_alerts(self, limit=200):
        ordered = sorted(
            self.rows.values(), key=lambda row: row.get("timestamp") or "", reverse=True
        )
        return ordered[:limit]

    def get_alert(self, alert_id):
        return self.rows.get(alert_id)

    def update_alert_status(self, alert_id, status):
        if status not in VALID_STATUSES:
            raise ValueError(f"Unrecognised status {status!r}")
        row = self.rows.get(alert_id)
        if row is None:
            return False
        row["status"] = status
        row["reviewedAt"] = datetime.now(timezone.utc).isoformat()
        print(f"  [dev] {row['ticker']} -> {status}")
        return True


def build_app():
    app = api_main.create_app()
    store = InMemoryDb(SEED)
    app.dependency_overrides[api_main.get_db] = lambda: store
    return app


if __name__ == "__main__":
    print("Trading Brain read API (development, in-memory alerts)")
    print("  http://localhost:8000/health")
    print("  http://localhost:8000/docs")
    print(f"  {len(SEED)} seeded alerts; market data is live from Yahoo Finance\n")
    uvicorn.run(build_app(), host="127.0.0.1", port=8000, log_level="info")
