"""Per-user approve/reject decisions on shared alerts.

Each signed-in email decides alone: the alert row keeps the owner's status, and this table keeps everyone's own
decision. The owner's existing decisions are copied in once by seed_owner, which is idempotent.
"""
import sqlite3
import threading
import time

from config import Config

DECISIONS = ("APPROVED", "REJECTED")


class DecisionStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS alert_decisions (alert_id TEXT NOT NULL, email TEXT NOT NULL, "
                         "decision TEXT NOT NULL, decided_at REAL NOT NULL, PRIMARY KEY (alert_id, email))")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, alert_id: str, email: str, decision: str) -> None:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO alert_decisions VALUES (?, ?, ?, ?)",
                         (alert_id, email.strip().lower(), decision, time.time()))

    def get(self, alert_id: str, email: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT decision FROM alert_decisions WHERE alert_id = ? AND email = ?",
                               (alert_id, email.strip().lower())).fetchone()
        return row[0] if row else None

    def for_email(self, email: str) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT alert_id, decision FROM alert_decisions WHERE email = ?",
                                (email.strip().lower(),)).fetchall()
        return dict(rows)

    def seed_owner(self, alerts: list[dict], owner_email: str) -> int:
        """Copy the owner's APPROVED/REJECTED alert statuses in; returns how many rows were new."""
        rows = [(a["alertId"], owner_email.strip().lower(), a["status"], time.time())
                for a in alerts if a.get("status") in DECISIONS]
        with self._lock, self._connect() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO alert_decisions VALUES (?, ?, ?, ?)", rows)
            return conn.total_changes - before


def effective_status(alert: dict, decision: str | None) -> str:
    """EXPIRED and INFO from the alert win; otherwise this user's decision; otherwise PENDING."""
    if alert.get("status") in ("EXPIRED", "INFO"):
        return alert["status"]
    return decision or "PENDING"


def decisions_store() -> DecisionStore:
    return DecisionStore(Config.SQLITE_PATH)
