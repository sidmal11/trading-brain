"""Expire every PENDING alert. Run once after an upgrade; sends nothing to Telegram.

    python scripts/clear_pending.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from utils.storage import alert_store  # noqa: E402

REASON = "Cleared at upgrade."


def main():
    store = alert_store()
    pending = [a["alertId"] for a in store.list_alerts(limit=10**9) if a.get("status") == "PENDING"]
    cleared = sum(store.expire_alert(alert_id, REASON) for alert_id in pending)
    print(f"Cleared {cleared} pending request(s).")


if __name__ == "__main__":
    main()
