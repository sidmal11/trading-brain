"""Who may sign in with an email code (P4): the allowed_emails table plus the owner, who manages it.

ALLOWED_EMAILS is copied into the table once, when the table is first created, so a later removal survives a
restart. The owner is OWNER_EMAIL (required with email sign-in) and can never be removed.
"""
import re
import sqlite3
import threading
import time

from config import Config

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise(email) -> str:
    return (email or "").strip().lower()


def valid_email(email) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.match(email))


def owner_email() -> str:
    """OWNER_EMAIL, normalised; "" when unset (email sign-in then refuses to start).

    No fallback to ALLOWED_EMAILS: reordering that list later would silently hand the list to someone else.
    """
    return normalise(Config.OWNER_EMAIL)


class AllowedEmailStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'allowed_emails'").fetchone()
            if not exists:
                conn.execute("CREATE TABLE allowed_emails (email TEXT PRIMARY KEY, added_by TEXT NOT NULL, "
                             "added_at REAL NOT NULL)")
                seeds = {normalise(e) for e in Config.ALLOWED_EMAILS.split(",") if e.strip()}
                conn.executemany("INSERT INTO allowed_emails VALUES (?, 'ALLOWED_EMAILS', ?)",
                                 [(e, time.time()) for e in sorted(seeds)])

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def list(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT email, added_by, added_at FROM allowed_emails ORDER BY email").fetchall()
        return [{"email": e, "addedBy": by, "addedAt": at} for e, by, at in rows]

    def contains(self, email) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM allowed_emails WHERE email = ?", (normalise(email),)).fetchone() is not None

    def add(self, email, added_by):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO allowed_emails VALUES (?, ?, ?)", (normalise(email), added_by, time.time()))

    def remove(self, email) -> None:
        """Delete the address and every session it holds (otp_sessions lives in the same file)."""
        email = normalise(email)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM allowed_emails WHERE email = ?", (email,))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'otp_sessions'").fetchone():
                conn.execute("DELETE FROM otp_sessions WHERE email = ?", (email,))


def access_store():
    return AllowedEmailStore(Config.SQLITE_PATH)


def is_allowed(email) -> bool:
    email = normalise(email)
    return bool(email) and (email == owner_email() or access_store().contains(email))
