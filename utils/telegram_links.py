"""Telegram chat-to-email links, made with one-time codes.

A signed-in email asks for a code, sends it to the bot, and the bot redeems it for that chat. A chat
belongs to one email at a time; linking it again moves it. Admin chats are the owner's, whatever the table says.
"""
import secrets
import sqlite3
import threading
import time

from api.access import normalise, owner_email
from config import Config

CODE_TTL_SECONDS = 600


class TelegramLinks:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS telegram_links (chat_id TEXT PRIMARY KEY, email TEXT NOT NULL, "
                         "linked_at REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS telegram_link_codes (code TEXT PRIMARY KEY, email TEXT NOT NULL, "
                         "expires_at REAL NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def new_code(self, email: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        code = secrets.token_urlsafe(12)
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO telegram_link_codes VALUES (?, ?, ?)",
                         (code, normalise(email), now + CODE_TTL_SECONDS))
        return code

    def redeem(self, code: str, chat_id: str, now: float | None = None) -> str | None:
        """The email the code belongs to, or None if unknown, expired or already used. A code is spent either way."""
        now = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT email, expires_at FROM telegram_link_codes WHERE code = ?", (code,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM telegram_link_codes WHERE code = ?", (code,))
            email, expires_at = row
            if now >= expires_at:
                return None
            conn.execute("INSERT OR REPLACE INTO telegram_links VALUES (?, ?, ?)", (str(chat_id), email, now))
            return email

    def email_for_chat(self, chat_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT email FROM telegram_links WHERE chat_id = ?", (str(chat_id),)).fetchone()
        return row[0] if row else None

    def chats_for_email(self, email: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chat_id FROM telegram_links WHERE email = ?", (normalise(email),)).fetchall()
        return [r[0] for r in rows]

    def unlink_email(self, email: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM telegram_links WHERE email = ?", (normalise(email),))

    def linked_emails(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT email FROM telegram_links").fetchall()
        return {r[0] for r in rows}


def owner_aware_email(links: TelegramLinks, chat_id: str, admin_chat_ids: set[str]) -> str | None:
    """Admin chats act as the owner; any other chat acts as the email it is linked to, if any."""
    if str(chat_id) in admin_chat_ids:
        return owner_email() or None
    return links.email_for_chat(chat_id)


def telegram_links() -> TelegramLinks:
    return TelegramLinks(Config.SQLITE_PATH)
