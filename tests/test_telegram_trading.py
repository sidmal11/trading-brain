"""Telegram Approve tap buys in the mock account, like the dashboard."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from tests.test_trading_api import Env  # noqa: E402
from utils import telegram_client as tc  # noqa: E402

ADMIN = "42"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}


class Http:
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json or {}))

        class R:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        return R()

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def tg(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path)
    http = Http()
    monkeypatch.setattr(api_main, "TelegramClient", tc.TelegramClient)
    monkeypatch.setattr(tc.requests, "post", http)
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "1:fake")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", ADMIN)
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    env.http = http

    def tap(decision="approve", alert_id="a1", ticker="AAPL"):
        return env.client.post("/api/v1/telegram/webhook", headers=HEADERS, json={"callback_query": {
            "id": "cb", "data": f"{decision}:{alert_id}:{ticker}", "from": {"id": int(ADMIN)},
            "message": {"message_id": 7, "chat": {"id": int(ADMIN)}},
        }})

    env.tap = tap
    return env


def _answers(http):
    return [p["text"] for m, p in http.calls if m == "answerCallbackQuery"]


def test_tap_approve_buys_and_replies(tg):
    assert tg.tap().status_code == 200
    assert "Bought 2.741228 AAPL at $182.40 ($500.00) (MOCK)." in tg.http.texts()  # the suggested quantity
    assert _answers(tg.http) == ["Bought 2.741228 AAPL (MOCK)"]
    assert "editMessageReplyMarkup" in tg.http.methods()
    orders = tg.client.get("/api/v1/orders").json()
    assert [o["status"] for o in orders] == ["FILLED"]
    assert tg.status() == "APPROVED"


def test_redelivered_tap_after_a_fill_below_the_stop_says_nothing_about_not_buying(tg):
    tg.alerts.save_alert("m1", "MSFT", "MACD", "x", trading_date="2026-11-17", stop=95.0, price=100.0)
    tg.price = 100.0
    tg.tap(alert_id="m1", ticker="MSFT")
    tg.price = 94.0
    tg.tap(alert_id="m1", ticker="MSFT")
    assert not any("not bought" in t for t in tg.http.texts())
    assert [o["status"] for o in tg.client.get("/api/v1/orders").json()] == ["FILLED"]
    assert tg.status("m1") == "APPROVED"


def test_tap_then_dashboard_approve_places_one_order(tg):
    tg.tap()
    r = tg.approve()
    orders = tg.client.get("/api/v1/orders").json()
    assert r.status_code == 200 and len(orders) == 1
    assert r.json()["order"]["id"] == orders[0]["id"] and orders[0]["status"] == "FILLED"


def test_refused_tap_replies_with_reason_and_keeps_buttons(tg):
    tg.price = None
    assert tg.tap().status_code == 200
    assert tg.http.texts() == ["AAPL not bought: Price unavailable."]
    assert _answers(tg.http) == ["Price unavailable."]
    assert "editMessageReplyMarkup" not in tg.http.methods()
    assert tg.status() == "PENDING"


def test_tap_with_trading_off_is_unchanged(tg):
    tg.set(tradingEnabled=False)
    assert tg.tap().status_code == 200
    assert tg.client.get("/api/v1/orders").json() == []
    assert not any("Bought" in t for t in tg.http.texts() + _answers(tg.http))
    assert _answers(tg.http) == ["AAPL: Approved"]
    assert tg.status() == "APPROVED"


def test_tap_reject_is_unchanged(tg):
    tg.tap("reject")
    assert tg.status() == "REJECTED"
    assert tg.client.get("/api/v1/orders").json() == []
    assert _answers(tg.http) == ["AAPL: Rejected"]


def test_tap_while_order_in_flight_does_not_claim_a_buy(tg):
    tg.trades.claim_buy("a1", "AAPL", 2, "2026-11-17")  # another request holds a PENDING order
    tg.tap()
    assert _answers(tg.http) == ["AAPL: order in progress."]
    assert tg.http.texts() == []
    assert tg.status() == "PENDING"


def test_tap_approve_unexpected_error_is_answered_not_redelivered(tg, monkeypatch):
    def locked(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(tg.trades, "fill", locked)
    r = tg.tap()
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    assert _answers(tg.http) == ["Broker error."]
    assert "AAPL not bought: Broker error." in tg.http.texts()
    assert all(o["status"] != "FILLED" for o in tg.trades.list_orders())
    assert tg.status() == "PENDING"


# ── Amounts (phase 1 task 4) ──────────────────────────────────────────────────

def _tap_data(tg, data, message_id=7):
    return tg.client.post("/api/v1/telegram/webhook", headers=HEADERS, json={"callback_query": {
        "id": "cb", "data": data, "from": {"id": int(ADMIN)},
        "message": {"message_id": message_id, "chat": {"id": int(ADMIN)}},
    }})


def _say(tg, text, chat_id=ADMIN):
    return tg.client.post("/api/v1/telegram/webhook", headers=HEADERS, json={"message": {
        "chat": {"id": int(chat_id), "type": "private"}, "from": {"id": int(chat_id)}, "text": text}})


def _keyboards(http):
    return [p.get("reply_markup") for m, p in http.calls if m == "sendMessage" and p.get("reply_markup")]


def test_preset_shares_button_buys_that_quantity(tg):
    _tap_data(tg, "bq:a1:0.5")
    assert "Bought 0.5 AAPL at $182.40 ($91.20) (MOCK)." in tg.http.texts()
    assert [o["quantity"] for o in tg.client.get("/api/v1/orders").json()] == [0.5]
    assert tg.status() == "APPROVED"


def test_preset_dollars_button_buys_that_amount(tg):
    _tap_data(tg, "bd:a1:10")
    assert [o["quantity"] for o in tg.client.get("/api/v1/orders").json()] == [0.054824]  # 10 / 182.4 rounded down


def test_other_amount_asks_then_confirms_then_buys(tg):
    _tap_data(tg, "ba:a1")
    assert tg.http.texts()[-1] == "Send shares (e.g. 5 or 0.25) or dollars (e.g. $10)"
    _say(tg, "$10")
    assert tg.http.texts()[-1] == "Buy 0.054824 AAPL, about $10.00 (MOCK)?"
    buttons = [b["callback_data"] for row in _keyboards(tg.http)[-1]["inline_keyboard"] for b in row]
    assert buttons == ["bc:a1:d10", "bx:a1"]
    assert tg.client.get("/api/v1/orders").json() == []  # nothing bought before Confirm
    _tap_data(tg, "bc:a1:d10", message_id=8)
    assert [o["quantity"] for o in tg.client.get("/api/v1/orders").json()] == [0.054824]


def test_typed_shares_and_trailing_dollar_sign_are_read(tg):
    _tap_data(tg, "ba:a1")
    _say(tg, "10$")
    assert tg.http.texts()[-1] == "Buy 0.054824 AAPL, about $10.00 (MOCK)?"
    _tap_data(tg, "ba:a1")
    _say(tg, "0.25")
    assert tg.http.texts()[-1] == "Buy 0.25 AAPL, about $45.60 (MOCK)?"


def test_unreadable_amount_keeps_the_prompt(tg):
    _tap_data(tg, "ba:a1")
    _say(tg, "lots")
    assert tg.http.texts()[-1] == "Could not read that amount. Send shares (e.g. 5 or 0.25) or dollars (e.g. $10)."
    _say(tg, "2")
    assert tg.http.texts()[-1] == "Buy 2 AAPL, about $364.80 (MOCK)?"


def test_amount_under_one_dollar_keeps_the_prompt(tg):
    _tap_data(tg, "ba:a1")
    _say(tg, "$0.50")
    assert tg.http.texts()[-1] == "Minimum order is $1."
    _say(tg, "$5")
    assert tg.http.texts()[-1].startswith("Buy 0.027412 AAPL")


def test_cancel_clears_without_buying(tg):
    _tap_data(tg, "ba:a1")
    _say(tg, "$10")
    _tap_data(tg, "bx:a1", message_id=8)
    assert _answers(tg.http)[-1] == "Cancelled."
    assert tg.client.get("/api/v1/orders").json() == [] and tg.status() == "PENDING"


def test_text_with_no_prompt_or_from_a_subscriber_does_nothing(tg):
    _say(tg, "$10")
    _tap_data(tg, "ba:a1")
    before = len(tg.http.calls)
    _say(tg, "$10", chat_id="99")  # a subscriber, not an admin
    assert len(tg.http.calls) == before


def test_prompt_older_than_ten_minutes_is_ignored(tg):
    _tap_data(tg, "ba:a1")
    with tg.alerts._connect() as conn:
        conn.execute("UPDATE telegram_prompts SET created_at = '2000-01-01T00:00:00+00:00'")
    before = len(tg.http.calls)
    _say(tg, "$10")
    assert len(tg.http.calls) == before


def test_confirm_on_an_expired_request_is_refused(tg):
    tg.alerts.expire_alert("a1", "Price fell to the stop $170.00.")
    _tap_data(tg, "bc:a1:s1")
    assert _answers(tg.http) == ["Request expired: Price fell to the stop $170.00."]
    assert tg.client.get("/api/v1/orders").json() == []


def test_commands_still_work_with_an_open_prompt(tg):
    _tap_data(tg, "ba:a1")
    r = _say(tg, "/stop")
    assert r.json().get("unsubscribed") == ADMIN


# ── Decisions are final (review findings) ─────────────────────────────────────

def test_leftover_confirm_cannot_buy_a_rejected_request(tg):
    _tap_data(tg, "ba:a1")
    _say(tg, "$10")
    tg.tap("reject")
    _tap_data(tg, "bc:a1:d10", message_id=8)
    assert _answers(tg.http)[-1] == "Already rejected."
    assert tg.client.get("/api/v1/orders").json() == [] and tg.status() == "REJECTED"


def test_reject_after_a_buy_is_refused(tg):
    _tap_data(tg, "bq:a1:0.5")
    tg.tap("reject")
    assert _answers(tg.http)[-1] == "Already bought. Close the position instead."
    assert tg.status() == "APPROVED"


def test_repeated_confirm_does_not_report_a_second_buy(tg):
    _tap_data(tg, "bc:a1:s0.5", message_id=8)
    _tap_data(tg, "bc:a1:s0.5", message_id=8)
    assert sum(t.startswith("Bought") for t in tg.http.texts()) == 1
    assert _answers(tg.http)[-1] == "Already bought 0.5 AAPL (MOCK)"
    assert len(tg.client.get("/api/v1/orders").json()) == 1



# ── P6: /pause and /resume ────────────────────────────────────────────────────

def test_admin_pause_and_resume_flip_mock_trading(tg):
    _say(tg, "/pause")
    assert tg.http.texts()[-1] == "Mock trading paused. Approvals will not buy until /resume."
    assert tg.client.get("/api/v1/settings").json()["tradingEnabled"] is False
    _say(tg, "/resume")
    assert tg.http.texts()[-1] == "Mock trading resumed."
    assert tg.client.get("/api/v1/settings").json()["tradingEnabled"] is True


def test_subscriber_pause_changes_nothing(tg):
    before = len(tg.http.calls)
    _say(tg, "/pause", chat_id="99")
    assert len(tg.http.calls) == before
    assert tg.client.get("/api/v1/settings").json()["tradingEnabled"] is True
