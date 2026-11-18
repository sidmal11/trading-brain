"""Storage backends for alerts and Telegram subscribers.

Two implementations with the same method names:

- DynamoDB (`DbClient`, `SubscriberStore`): the AWS deployment, unchanged.
- SQLite (`SqliteStore`): a single file, standard library only. For a host without
  AWS. Chosen with STORAGE_BACKEND=sqlite and SQLITE_PATH.

Callers use `alert_store()` and `subscriber_store()` so the backend is picked in
one place. The SQLite store keeps each alert as a JSON document with the same
field names the DynamoDB rows use, so the API and dashboard read both the same way.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import Config

STATUS_PENDING = "PENDING"
STATUS_EXPIRED = "EXPIRED"
STATUS_INFO = "INFO"  # stored for the dashboard; its strategy sends no buy requests, so it takes no decision
VALID_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", STATUS_EXPIRED, STATUS_INFO})
PROMPT_TTL = timedelta(minutes=10)  # a typed Telegram amount counts only this long after "Other amount"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    """Alerts and subscribers in one SQLite file. Methods mirror DbClient and SubscriberStore."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS alerts (alert_id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS subscribers (chat_id TEXT PRIMARY KEY, active INTEGER NOT NULL, doc TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS notices (key TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS telegram_prompts "
                         "(chat_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL, created_at TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    # ── Alerts (DbClient interface) ──────────────────────────────────────────

    def save_alert(self, alert_id, ticker, signal_details, pros_cons, chat_id=None, message_id=None,
                   price=None, strategy="volume", trading_date=None, signal_type="VOLUME_BREAKTHROUGH",
                   confidence=None, checklist=None, entry=None, stop=None, target=None, messages=None,
                   status=STATUS_PENDING):
        item = {
            "alertId": alert_id,
            "ticker": ticker,
            "timestamp": _now_iso(),
            "signalDetails": signal_details,
            "aiProsCons": pros_cons,
            "status": status,
            "strategy": strategy,
            "signalType": signal_type,
        }
        extra = {"confidence": confidence, "checklist": checklist, "entryPrice": entry, "stopPrice": stop,
                 "targetPrice": target}
        item.update({k: v for k, v in extra.items() if v is not None})
        if trading_date:
            item["tradingDate"] = trading_date
        if price is not None:
            item["priceAtAlert"] = price
        if chat_id:
            item["telegramChatId"] = chat_id
        if message_id:
            item["telegramMessageId"] = message_id
        if messages:
            item["telegramMessages"] = messages
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO alerts (alert_id, doc) VALUES (?, ?)",
                (alert_id, json.dumps(item)),
            )
        return True

    def list_alerts(self, limit=200):
        with self._connect() as conn:
            rows = conn.execute("SELECT doc FROM alerts").fetchall()
        items = [json.loads(r[0]) for r in rows][:limit]
        return sorted(items, key=lambda i: i.get("timestamp") or "", reverse=True)

    def alert_exists(self, ticker, strategy, trading_date):
        """True when an alert for this ticker, strategy and trading date is stored."""
        return any(
            a.get("ticker") == ticker and a.get("strategy") == strategy and a.get("tradingDate") == trading_date
            for a in self.list_alerts(limit=10**9)
        )

    def get_alert(self, alert_id):
        with self._connect() as conn:
            row = conn.execute("SELECT doc FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_alert_status(self, alert_id, status):
        if status not in VALID_STATUSES:
            raise ValueError(f"Unrecognised status {status!r}. Expected one of {sorted(VALID_STATUSES)}.")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT doc FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
            if row is None:
                return False
            item = json.loads(row[0])
            if item.get("status") == STATUS_EXPIRED:
                return False  # an expired request is final
            item["status"] = status
            item["reviewedAt"] = _now_iso()
            conn.execute("UPDATE alerts SET doc = ? WHERE alert_id = ?", (json.dumps(item), alert_id))
        return True

    def mark_notice(self, key):
        """True the first time `key` is marked, False after (once-per-day messages survive a restart)."""
        with self._lock, self._connect() as conn:
            return conn.execute("INSERT OR IGNORE INTO notices (key, sent_at) VALUES (?, ?)",
                                (key, _now_iso())).rowcount == 1

    # ── Telegram amount prompts ──────────────────────────────────────────────

    def set_prompt(self, chat_id, alert_id):
        """Remember that this chat's next typed amount is for `alert_id` (replaces any earlier prompt)."""
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO telegram_prompts (chat_id, alert_id, created_at) VALUES (?, ?, ?)",
                         (str(chat_id), alert_id, _now_iso()))

    def get_prompt(self, chat_id):
        """The alert id this chat was asked an amount for, or None when there is none or it is over 10 minutes old."""
        with self._connect() as conn:
            row = conn.execute("SELECT alert_id, created_at FROM telegram_prompts WHERE chat_id = ?",
                               (str(chat_id),)).fetchone()
        if not row or datetime.now(timezone.utc) - datetime.fromisoformat(row[1]) > PROMPT_TTL:
            return None
        return row[0]

    def clear_prompt(self, chat_id):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM telegram_prompts WHERE chat_id = ?", (str(chat_id),))

    def expire_alert(self, alert_id, reason):
        """PENDING -> EXPIRED with a reason. Returns whether it changed."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT doc FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
            item = json.loads(row[0]) if row else None
            if item is None or item.get("status") != "PENDING":
                return False
            item.update(status=STATUS_EXPIRED, expiredReason=reason, expiredAt=_now_iso())
            conn.execute("UPDATE alerts SET doc = ? WHERE alert_id = ?", (json.dumps(item), alert_id))
        return True

    # ── Subscribers (SubscriberStore interface) ──────────────────────────────

    def subscribe(self, chat_id, username=None, first_name=None):
        doc = {"chatId": str(chat_id), "active": True, "subscribedAt": _now_iso()}
        if username:
            doc["username"] = username
        if first_name:
            doc["firstName"] = first_name
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO subscribers (chat_id, active, doc) VALUES (?, 1, ?)",
                (str(chat_id), json.dumps(doc)),
            )
        return True

    def deactivate(self, chat_id):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT doc FROM subscribers WHERE chat_id = ?", (str(chat_id),)).fetchone()
            if row is None:
                return False
            doc = json.loads(row[0])
            doc["active"] = False
            conn.execute(
                "UPDATE subscribers SET active = 0, doc = ? WHERE chat_id = ?",
                (json.dumps(doc), str(chat_id)),
            )
        return True

    def active_chat_ids(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT chat_id FROM subscribers WHERE active = 1").fetchall()
        return [r[0] for r in rows]


def alert_store():
    """The alert store for the configured backend."""
    if Config.STORAGE_BACKEND == "sqlite":
        return SqliteStore(Config.SQLITE_PATH)
    from utils.db_client import DbClient
    return DbClient()


def subscriber_store():
    """The subscriber store for the configured backend."""
    if Config.STORAGE_BACKEND == "sqlite":
        return SqliteStore(Config.SQLITE_PATH)
    from utils.subscriber_store import SubscriberStore
    return SubscriberStore()
