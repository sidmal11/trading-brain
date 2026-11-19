"""P4 access list: owner-only management, sign-in honours the table, removal ends sessions at once."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import access  # noqa: E402
from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from tests.test_email_otp import ALLOWED, _login, env  # noqa: E402,F401  (env is a fixture)

FRIEND = "friend@example.com"


def _client_for(env, email):
    """A second browser: its own cookie jar, signed in as `email`."""
    other = TestClient(env["client"].app)
    other.post("/api/v1/auth/otp/request", json={"email": email})
    other.post("/api/v1/auth/otp/verify", json={"email": email, "code": env["outbox"].last_code()})
    return other


def test_seed_is_copied_once_and_a_removal_survives_a_restart(env, monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_EMAILS", f"{ALLOWED}, Friend@Example.com ")
    path = str(env["path"]).replace("auth.db", "seed.db")
    store = access.AllowedEmailStore(path)
    assert [e["email"] for e in store.list()] == [FRIEND, ALLOWED]
    store.remove(FRIEND)
    assert [e["email"] for e in access.AllowedEmailStore(path).list()] == [ALLOWED]  # not re-seeded


def test_owner_is_only_owner_email(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_EMAILS", "b@x.com, a@x.com")
    monkeypatch.setattr(Config, "OWNER_EMAIL", "")
    assert access.owner_email() == ""  # never taken from ALLOWED_EMAILS
    monkeypatch.setattr(Config, "OWNER_EMAIL", " Boss@X.com ")
    assert access.owner_email() == "boss@x.com"


@pytest.mark.parametrize("email, ok", [
    ("a@b.co", True), ("first.last+tag@sub.example.org", True),
    ("no-at.example.com", False), ("a@b", False), ("a @b.com", False), ("a@@b.com", False), ("", False),
    ("x" * 250 + "@b.com", False),
])
def test_email_format_check(email, ok):
    assert access.valid_email(email) is ok


def test_owner_lists_adds_and_removes(env):
    _login(env)
    c = env["client"]
    assert c.get("/api/v1/allowed-emails").json() == {
        "owner": ALLOWED, "emails": [{"email": ALLOWED, "addedBy": "ALLOWED_EMAILS", "addedAt": pytest.approx(0, abs=1e12)}]}
    r = c.post("/api/v1/allowed-emails", json={"email": "  Friend@Example.COM "})
    assert r.status_code == 200 and [e["email"] for e in r.json()["emails"]] == [FRIEND, ALLOWED]
    assert r.json()["emails"][0]["addedBy"] == ALLOWED
    assert c.post("/api/v1/allowed-emails", json={"email": FRIEND}).status_code == 200  # already there: unchanged
    assert c.post("/api/v1/allowed-emails", json={"email": "not-an-email"}).status_code == 422
    r = c.delete(f"/api/v1/allowed-emails/{FRIEND}")
    assert r.status_code == 200 and [e["email"] for e in r.json()["emails"]] == [ALLOWED]


def test_owner_cannot_be_removed(env):
    _login(env)
    r = env["client"].delete(f"/api/v1/allowed-emails/{ALLOWED.upper()}")
    assert r.status_code == 409 and r.json()["detail"] == "The owner cannot be removed."
    assert access.is_allowed(ALLOWED)


def test_added_address_can_sign_in_and_a_non_owner_gets_403(env):
    _login(env)
    env["client"].post("/api/v1/allowed-emails", json={"email": FRIEND})
    friend = _client_for(env, "FRIEND@example.com")  # any casing signs in
    assert friend.get("/api/v1/auth/me").json() == {"email": FRIEND, "owner": False}
    assert friend.get("/api/v1/settings").status_code == 200  # a normal page works
    for call in (lambda: friend.get("/api/v1/allowed-emails"),
                 lambda: friend.post("/api/v1/allowed-emails", json={"email": "x@y.com"}),
                 lambda: friend.delete(f"/api/v1/allowed-emails/{ALLOWED}")):
        r = call()
        assert r.status_code == 403 and r.json()["detail"] == "Only the owner can manage access."
    assert [e["email"] for e in access.access_store().list()] == [FRIEND, ALLOWED]


def test_removal_ends_that_persons_session_at_once(env):
    _login(env)
    env["client"].post("/api/v1/allowed-emails", json={"email": FRIEND})
    friend = _client_for(env, FRIEND)
    assert friend.get("/api/v1/settings").status_code == 200
    env["client"].delete(f"/api/v1/allowed-emails/{FRIEND}")
    assert friend.get("/api/v1/settings").status_code == 401
    assert friend.get("/api/v1/auth/me").status_code == 401


def test_unknown_address_gets_no_code_and_the_same_answer(env):
    r = env["client"].post("/api/v1/auth/otp/request", json={"email": "stranger@example.com"})
    assert r.status_code == 200 and r.json() == {"ok": True} and env["outbox"].sent == []


def test_signed_out_gets_401_and_no_email_mode_gets_404(env, monkeypatch):
    assert env["client"].get("/api/v1/allowed-emails").status_code == 401
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    assert TestClient(api_main.create_app()).get("/api/v1/allowed-emails").status_code == 404
