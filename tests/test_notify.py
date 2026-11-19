"""P2: Telegram confirmations for dashboard buys, refusals and closes; the signal's alert and buy prices."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from api import notify  # noqa: E402
from tests.test_trading_api import Env  # noqa: E402


def test_messages_use_the_spec_wording():
    order = {"ticker": "AAPL", "quantity": 2.741228, "fillPrice": 182.4}
    assert notify.bought_text(order, {"priceAtAlert": 180.1}) == \
        "Bought 2.741228 AAPL at $182.40 ($500.00) (MOCK). Alerted at $180.10."
    assert notify.bought_text(order, {}) == "Bought 2.741228 AAPL at $182.40 ($500.00) (MOCK)."
    assert notify.bought_text(order, {"priceAtAlert": "n/a"}) == "Bought 2.741228 AAPL at $182.40 ($500.00) (MOCK)."
    assert notify.refused_text("AAPL", "Market is closed.") == "AAPL not bought: Market is closed."
    sell = {"ticker": "AAPL", "quantity": 2, "fillPrice": 190.0}
    assert notify.sold_text(sell, 364.8) == "Sold 2 AAPL at $190.00 (MOCK). P/L +$15.20."
    assert notify.sold_text(sell, 400.0) == "Sold 2 AAPL at $190.00 (MOCK). P/L -$20.00."
    assert notify.sold_text(sell, None) == "Sold 2 AAPL at $190.00 (MOCK)."


def test_admins_gets_every_admin_chat_and_survives_a_failure():
    sent = []

    class Tg:
        admin_chat_ids = {"2", "1"}

        def send_text(self, chat_id, text):
            if chat_id == "1":
                raise RuntimeError("Telegram down")
            sent.append((chat_id, text))

    notify.admins("hello", Tg())
    assert sent == [("2", "hello")]


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    e.sent = []
    monkeypatch.setattr(api_main.notify, "admins", lambda text: e.sent.append(text))
    return e


def test_dashboard_buy_sends_one_confirmation(env):
    assert env.approve().status_code == 200
    assert env.approve().status_code == 200  # a repeat approval answers with the same order
    assert env.sent == ["Bought 2.741228 AAPL at $182.40 ($500.00) (MOCK)."]


def test_dashboard_refusal_is_sent_but_a_decided_one_is_not(env):
    env.approve()
    env.save_alert("a2")  # AAPL again while it is held
    env.sent.clear()
    r = env.approve("a2")
    assert r.status_code == 409 and env.sent == ["AAPL not bought: Already holding AAPL."]
    env.sent.clear()
    env.save_alert("a3", ticker="MSFT")
    env.client.post("/api/v1/alerts/a3/review", json={"decision": "REJECTED"})
    assert env.approve("a3").status_code == 409  # already rejected: nothing to retry
    assert env.sent == []


def test_trading_off_sends_nothing(env):
    env.set(tradingEnabled=False)
    assert env.approve().status_code == 200 and env.sent == []


def test_close_sends_the_sale_with_profit(env):
    env.approve()
    env.sent.clear()
    env.price = 200.0
    r = env.client.post("/api/v1/positions/aapl/close")
    assert r.status_code == 200
    assert env.sent == ["Sold 2.741228 AAPL at $200.00 (MOCK). P/L +$48.25."]


def test_buy_price_is_the_filled_order_of_that_alert(env):
    assert api_main._buy_price("a1") is None
    env.approve()
    assert api_main._buy_price("a1") == 182.4
    assert api_main._buy_price("nope") is None
