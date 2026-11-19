"""Email one-time-code login. No real email is sent: a recorder captures the code."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import auth as auth_module  # noqa: E402
from api import email_otp  # noqa: E402
from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402

ALLOWED = "trader@example.com"
SECRET = "x" * 40


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class Outbox:
    def __init__(self):
        self.sent = []  # (to, code)

    def __call__(self, to, code):
        self.sent.append((to, code))

    def last_code(self):
        return self.sent[-1][1]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "AUTH_MODE", "email_otp")
    monkeypatch.setattr(Config, "ALLOWED_EMAILS", ALLOWED)
    monkeypatch.setattr(Config, "OWNER_EMAIL", ALLOWED)
    monkeypatch.setattr(Config, "SESSION_SECRET", SECRET)
    monkeypatch.setattr(Config, "COOKIE_SECURE", False)  # the test client is plain http
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")

    clock, outbox = Clock(), Outbox()
    service = email_otp.EmailOtpService(email_otp.OtpStore(str(tmp_path / "auth.db")), sender=outbox, clock=clock)
    monkeypatch.setattr(auth_module, "_otp_service", service)
    monkeypatch.setattr(api_main, "_db_client", None)
    monkeypatch.setattr(api_main, "_subscriber_store", None)

    client = TestClient(api_main.create_app())
    return {"client": client, "clock": clock, "outbox": outbox, "service": service, "path": tmp_path / "auth.db"}


def _login(env, email=ALLOWED):
    env["client"].post("/api/v1/auth/otp/request", json={"email": email})
    return env["client"].post("/api/v1/auth/otp/verify", json={"email": email, "code": env["outbox"].last_code()})


# ── Request ───────────────────────────────────────────────────────────────────

def test_allowed_email_gets_a_code(env):
    r = env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    assert r.status_code == 200
    assert [to for to, _ in env["outbox"].sent] == [ALLOWED]


def test_unknown_email_gets_the_same_answer_and_no_email(env):
    r = env["client"].post("/api/v1/auth/otp/request", json={"email": "stranger@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert env["outbox"].sent == []


def test_second_request_within_cooldown_sends_nothing_new(env):
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    assert len(env["outbox"].sent) == 1


def test_code_is_not_stored_in_plain_text(env):
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    code = env["outbox"].last_code()
    assert code.encode() not in env["path"].read_bytes()


# ── Verify and session ────────────────────────────────────────────────────────

def test_correct_code_sets_a_secure_session_cookie(env):
    r = _login(env)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "tb_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_session_cookie_unlocks_the_data_api(env):
    _login(env)
    assert env["client"].get("/api/v1/alerts").status_code == 200


def test_no_cookie_means_no_data(env):
    assert env["client"].get("/api/v1/alerts").status_code == 401


def test_wrong_code_is_refused(env):
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    r = env["client"].post("/api/v1/auth/otp/verify", json={"email": ALLOWED, "code": "000000"})
    assert r.status_code == 401


def test_code_locks_after_five_wrong_attempts(env):
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    right = env["outbox"].last_code()
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(5):
        env["client"].post("/api/v1/auth/otp/verify", json={"email": ALLOWED, "code": wrong})
    r = env["client"].post("/api/v1/auth/otp/verify", json={"email": ALLOWED, "code": right})
    assert r.status_code == 401  # locked, even with the right code


def test_code_expires_after_ten_minutes(env):
    env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    code = env["outbox"].last_code()
    env["clock"].now += 11 * 60
    r = env["client"].post("/api/v1/auth/otp/verify", json={"email": ALLOWED, "code": code})
    assert r.status_code == 401


def test_a_code_works_once(env):
    _login(env)
    code = env["outbox"].last_code()
    r = env["client"].post("/api/v1/auth/otp/verify", json={"email": ALLOWED, "code": code})
    assert r.status_code == 401


def test_session_expires_after_seven_days(env):
    _login(env)
    env["clock"].now += 8 * 24 * 3600
    assert env["client"].get("/api/v1/alerts").status_code == 401


def test_logout_ends_the_session(env):
    _login(env)
    env["client"].post("/api/v1/auth/logout")
    assert env["client"].get("/api/v1/alerts").status_code == 401


# ── Startup safety ────────────────────────────────────────────────────────────

def test_app_refuses_to_start_with_a_short_secret(env, monkeypatch):
    monkeypatch.setattr(Config, "SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        api_main.create_app()


def test_app_refuses_to_start_with_no_owner(env, monkeypatch):
    monkeypatch.setattr(Config, "OWNER_EMAIL", "  ")
    with pytest.raises(RuntimeError, match="needs OWNER_EMAIL"):
        api_main.create_app()


def test_auth_routes_are_404_when_email_login_is_off(env, monkeypatch):
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    r = env["client"].post("/api/v1/auth/otp/request", json={"email": ALLOWED})
    assert r.status_code == 404


def test_me_reports_the_signed_in_email(env):
    assert env["client"].get("/api/v1/auth/me").status_code == 401
    _login(env)
    assert env["client"].get("/api/v1/auth/me").json() == {"email": ALLOWED, "owner": True}
