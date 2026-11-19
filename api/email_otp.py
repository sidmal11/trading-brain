"""Email one-time-code login with a session cookie. Used when AUTH_MODE=email_otp.

Flow:
1. POST /api/v1/auth/otp/request {email}: if the address is allowed (api/access.py), a
   6-digit code is emailed. The response is identical either way, so the API
   does not reveal which addresses are allowed.
2. POST /api/v1/auth/otp/verify {email, code}: on success, sets an HttpOnly session
   cookie that the dashboard sends on every request.
3. POST /api/v1/auth/logout: ends the session.

Stored data holds no plain secrets: codes and session tokens are kept as HMAC or
SHA-256 hashes. Codes expire after 10 minutes and lock after 5 wrong attempts.
Sessions last 7 days.
"""

import hashlib
import hmac
import logging
import secrets
import smtplib
import sqlite3
import threading
import time
from email.message import EmailMessage

from config import Config

logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = 10 * 60
CODE_MAX_ATTEMPTS = 5
REQUEST_COOLDOWN_SECONDS = 60
SESSION_TTL_SECONDS = 7 * 24 * 3600
COOKIE_NAME = "tb_session"


def _now():
    return time.time()


class OtpStore:
    """SQLite-backed codes and sessions. Shares the storage file with the alerts."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS otp_codes ("
                " email TEXT PRIMARY KEY, code_hash TEXT NOT NULL,"
                " expires_at REAL NOT NULL, attempts INTEGER NOT NULL, created_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS otp_sessions ("
                " token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def put_code(self, email, code_hash, created_at):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO otp_codes (email, code_hash, expires_at, attempts, created_at)"
                " VALUES (?, ?, ?, 0, ?)",
                (email, code_hash, created_at + CODE_TTL_SECONDS, created_at),
            )

    def get_code(self, email):
        with self._connect() as conn:
            return conn.execute(
                "SELECT code_hash, expires_at, attempts, created_at FROM otp_codes WHERE email = ?", (email,)
            ).fetchone()

    def bump_attempts(self, email):
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE email = ?", (email,))

    def delete_code(self, email):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM otp_codes WHERE email = ?", (email,))

    def put_session(self, token_hash, email, expires_at):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO otp_sessions (token_hash, email, expires_at) VALUES (?, ?, ?)",
                (token_hash, email, expires_at),
            )

    def get_session(self, token_hash):
        with self._connect() as conn:
            return conn.execute(
                "SELECT email, expires_at FROM otp_sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()

    def delete_session(self, token_hash):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM otp_sessions WHERE token_hash = ?", (token_hash,))


def _normalise(email: str) -> str:
    return (email or "").strip().lower()


def _code_hash(email: str, code: str) -> str:
    return hmac.new(Config.SESSION_SECRET.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def send_code_email(to: str, code: str) -> None:
    """Send the code by SMTP. With no SMTP host and OTP_DEV_PRINT=1, log it instead (local only)."""
    if not Config.SMTP_HOST:
        if Config.OTP_DEV_PRINT:
            logger.warning("OTP_DEV_PRINT: code for %s is %s (local development only)", to, code)
            return
        raise RuntimeError("SMTP_HOST is not set; cannot send login codes.")

    message = EmailMessage()
    message["Subject"] = "Your Trading Brain login code"
    message["From"] = Config.SMTP_FROM
    message["To"] = to
    message.set_content(
        f"Your login code is {code}. It expires in 10 minutes.\n\n"
        "If you did not ask for this, ignore this email."
    )
    with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as smtp:
        smtp.starttls()
        if Config.SMTP_USER:
            smtp.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        smtp.send_message(message)


class EmailOtpService:
    def __init__(self, store, sender=send_code_email, clock=_now):
        self.store = store
        self.sender = sender
        self.clock = clock

    def request_code(self, email: str) -> None:
        """Always returns None. Callers respond identically whether or not the email is allowed."""
        from .access import is_allowed

        email = _normalise(email)
        if not is_allowed(email):
            return
        row = self.store.get_code(email)
        if row and self.clock() - row[3] < REQUEST_COOLDOWN_SECONDS:
            return  # a code was sent a moment ago; do not spam

        code = f"{secrets.randbelow(1_000_000):06d}"
        self.store.put_code(email, _code_hash(email, code), self.clock())
        try:
            self.sender(email, code)
        except Exception:
            # Do not tell the caller: that would reveal the address is allowed.
            logger.exception("Failed to send login code email")
            self.store.delete_code(email)

    def verify_code(self, email: str, code: str):
        """Return a session token on success, or None."""
        email = _normalise(email)
        row = self.store.get_code(email)
        if row is None:
            return None
        expected_hash, expires_at, attempts, _created = row
        if self.clock() > expires_at or attempts >= CODE_MAX_ATTEMPTS:
            self.store.delete_code(email)
            return None

        if not hmac.compare_digest(expected_hash, _code_hash(email, (code or "").strip())):
            self.store.bump_attempts(email)
            return None

        self.store.delete_code(email)
        token = secrets.token_urlsafe(32)
        self.store.put_session(_token_hash(token), email, self.clock() + SESSION_TTL_SECONDS)
        return token

    def session_email(self, token: str):
        """The email for a valid session token, or None."""
        if not token:
            return None
        row = self.store.get_session(_token_hash(token))
        if row is None:
            return None
        email, expires_at = row
        if self.clock() > expires_at:
            return None
        return email

    def end_session(self, token: str) -> None:
        if token:
            self.store.delete_session(_token_hash(token))
