"""Every trade, decision and report is the caller's own: one person's action never touches another's account."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from tests.test_trading_api import Env, _NoopTelegram  # noqa: E402

OFF = "Mock trading is turned off for your account. Ask the owner."


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def test_two_users_approve_the_same_alert_into_their_own_accounts(env):
    env.set(tradingEnabled=True); env.approvals_buy("a@x.com"); env.approvals_buy("b@x.com")
    ra, rb = env.as_user("a@x.com").approve(), env.as_user("b@x.com").approve()
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["order"]["accountId"] != rb.json()["order"]["accountId"]


def test_approval_records_only_without_all_three_switches(env):
    env.set(tradingEnabled=True)                      # approvals_buy is off by default
    r = env.as_user("a@x.com").approve()
    assert r.json()["order"] is None
    assert env.signals_for("a@x.com")["a1"] == "APPROVED" and env.signals_for("b@x.com")["a1"] == "PENDING"


def test_users_may_disagree_and_only_the_owner_sets_the_global_status(env):
    env.as_user("b@x.com").reject()
    assert env.status() == "PENDING" and _NoopTelegram.calls == []
    env.as_user(env.owner).approve()
    assert env.status() == "APPROVED"


def test_disabled_account_cannot_buy_but_can_close(env):
    env.as_user("a@x.com").paper_buy("MSFT", dollars=100); env.disable("a@x.com")
    r = env.as_user("a@x.com").paper_buy("MSFT", dollars=100)
    assert r.status_code == 409 and r.json()["detail"] == "Mock trading is turned off for your account. Ask the owner."
    assert env.as_user("a@x.com").close("MSFT").status_code == 200


def test_positions_and_orders_are_the_callers(env):
    env.as_user("a@x.com").paper_buy("MSFT", dollars=100)
    assert env.as_user("b@x.com").client.get("/api/v1/orders").json() == []


# ── Beyond the brief: the rest of the per-user rules ──────────────────────────

def test_a_user_may_approve_what_the_owner_rejected_without_editing_telegram(env):
    env.approvals_buy("a@x.com")
    env.as_user(env.owner).reject()
    _NoopTelegram.calls = []
    r = env.as_user("a@x.com").approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"
    assert env.status() == "REJECTED" and _NoopTelegram.calls == []
    assert env.signals_for(env.owner)["a1"] == "REJECTED" and env.signals_for("a@x.com")["a1"] == "APPROVED"


def test_already_rejected_and_already_bought_are_per_user(env):
    env.approvals_buy("a@x.com")
    env.as_user("b@x.com").reject()
    r = env.as_user("b@x.com").approve()
    assert r.status_code == 409 and r.json()["detail"] == "Already rejected."
    assert env.as_user("a@x.com").approve().status_code == 200
    r = env.as_user("a@x.com").reject()
    assert r.status_code == 409 and r.json()["detail"] == "Already bought. Close the position instead."
    assert env.as_user(env.owner).reject().status_code == 200   # the owner bought nothing, so may reject


def test_repeat_approval_returns_the_same_order(env):
    env.approvals_buy("a@x.com")
    first, second = env.as_user("a@x.com").approve(), env.as_user("a@x.com").approve()
    assert first.json()["order"]["id"] == second.json()["order"]["id"]


def test_the_daily_limit_and_cash_are_per_account(env):
    env.set(maxOrdersPerDay=1); env.approvals_buy("a@x.com")
    assert env.as_user(env.owner).approve().json()["order"]["status"] == "FILLED"
    assert env.as_user("a@x.com").approve().json()["order"]["status"] == "FILLED"
    owner = env.as_user(env.owner).client.get("/api/v1/positions").json()
    mine = env.as_user("a@x.com").client.get("/api/v1/positions").json()
    assert owner["cash"] == mine["cash"] < 10000 and len(owner["positions"]) == len(mine["positions"]) == 1


def test_reset_and_close_touch_only_the_callers_account(env):
    env.as_user("a@x.com").paper_buy("MSFT", dollars=100)
    env.as_user("b@x.com").paper_buy("MSFT", dollars=100)
    assert env.as_user("b@x.com").close("MSFT").status_code == 200
    assert env.as_user("b@x.com").client.post("/api/v1/paper/reset", json={}).status_code == 200
    [position] = env.as_user("a@x.com").client.get("/api/v1/positions").json()["positions"]
    assert position["ticker"] == "MSFT"
    assert len(env.as_user("a@x.com").client.get("/api/v1/orders").json()) == 1


def test_disabled_account_cannot_reset_and_its_approvals_only_record(env):
    env.approvals_buy("a@x.com"); env.disable("a@x.com")
    r = env.as_user("a@x.com").client.post("/api/v1/paper/reset", json={})
    assert r.status_code == 409 and r.json()["detail"] == OFF
    r = env.as_user("a@x.com").approve()
    assert r.status_code == 200 and r.json()["order"] is None


def test_my_account_reads_and_sets_approvals_buy(env):
    me = env.as_user("a@x.com").client.get("/api/v1/me/account").json()
    assert (me["kind"], me["enabled"], me["approvalsBuy"], me["telegramLinked"]) == ("mock", True, False, False)
    r = env.as_user("a@x.com").client.put("/api/v1/me/account", json={"approvalsBuy": True})
    assert r.status_code == 200 and r.json()["approvalsBuy"] is True
    assert env.as_user("b@x.com").client.get("/api/v1/me/account").json()["approvalsBuy"] is False


def test_trade_confirmations_go_to_the_callers_email(env, monkeypatch):
    sent = []
    monkeypatch.setattr(api_main.notify, "to_email", lambda email, text: sent.append((email, text)))
    env.approvals_buy("a@x.com")
    env.as_user("a@x.com").approve()
    assert [e for e, _ in sent] == ["a@x.com"]


def test_to_email_sends_to_linked_chats_and_admins_only_for_the_owner(env):
    from api import notify
    from utils.telegram_links import telegram_links

    links = telegram_links()
    links.redeem(links.new_code("a@x.com"), "77")
    sent = []

    class Tg:
        admin_chat_ids = {"1"}

        def send_text(self, chat_id, text):
            sent.append(chat_id)

    notify.to_email("a@x.com", "hi", Tg())
    notify.to_email("b@x.com", "hi", Tg())
    notify.to_email(env.owner, "hi", Tg())
    assert sent == ["77", "1"]


def test_owner_decisions_are_seeded_at_start(monkeypatch, tmp_path):
    from utils.decisions_store import decisions_store
    from utils.storage import SqliteStore
    from config import Config

    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(Config, "OWNER_EMAIL", "owner@x.com")
    store = SqliteStore(Config.SQLITE_PATH)
    store.save_alert("r1", "AAPL", "x", "y", trading_date="2026-11-17")
    store.update_alert_status("r1", "REJECTED")
    api_main.create_app()
    assert decisions_store().get("r1", "owner@x.com") == "REJECTED"


def test_the_approval_queue_shows_each_users_own_status(env):
    env.as_user("b@x.com").reject()
    alerts = lambda email: {a["alertId"]: a["status"] for a in env.as_user(email).client.get("/api/v1/alerts").json()}
    assert alerts("b@x.com")["a1"] == "REJECTED" and alerts(env.owner)["a1"] == "PENDING"
    overview = lambda email: env.as_user(email).client.get("/api/v1/market-overview").json()["pendingApprovalsCount"]
    env.mp.setattr(api_main.market_source, "index_quote", lambda symbol: None)
    assert (overview("b@x.com"), overview(env.owner)) == (0, 1)


# -- Final review fixes --

def test_owner_approval_buys_after_deploy_when_trading_was_on(monkeypatch, tmp_path):
    from tests.test_trading_api import Env as _Env

    env = _Env(monkeypatch, tmp_path, preset_owner=False)  # no switch set by hand: the migration created it
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"


def test_non_owner_start_over_uses_the_owner_set_starting_cash(env):
    env.as_user("a@x.com").paper_buy("MSFT", dollars=100)
    r = env.as_user("a@x.com").client.post("/api/v1/paper/reset", json={"startingCash": 50000})
    assert r.status_code == 403 and r.json()["detail"] == "Only the owner can change your starting cash."
    r = env.as_user("a@x.com").client.post("/api/v1/paper/reset", json={})
    assert r.status_code == 200 and (r.json()["cash"], r.json()["startingCash"]) == (10000.0, 10000.0)
    assert env.as_user("a@x.com").client.post("/api/v1/paper/reset", json={"startingCash": 10000}).status_code == 200


def test_owner_start_over_may_choose_the_amount(env):
    r = env.as_user(env.owner).client.post("/api/v1/paper/reset", json={"startingCash": 25000})
    assert r.status_code == 200 and r.json()["startingCash"] == 25000


def test_approval_after_turning_the_switch_on_buys_and_confirms(env, monkeypatch):
    sent = []
    monkeypatch.setattr(api_main.notify, "to_email", lambda email, text: sent.append((email, text)))
    assert env.as_user("a@x.com").approve().json()["order"] is None  # switch off: records only
    env.approvals_buy("a@x.com")
    r = env.as_user("a@x.com").approve()
    assert r.json()["order"]["status"] == "FILLED" and [e for e, _ in sent] == ["a@x.com"]


def test_to_email_still_reaches_the_admin_chats_when_links_cannot_be_read(env, monkeypatch):
    from api import notify
    from utils import telegram_links

    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(telegram_links, "telegram_links", broken)
    sent = []

    class Tg:
        admin_chat_ids = {"1"}

        def send_text(self, chat_id, text):
            sent.append(chat_id)

    notify.to_email(env.owner, "hi", Tg())
    assert sent == ["1"]
