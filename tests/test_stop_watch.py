"""Telegram warning when a held mock position nears or breaks its stop. Never sells."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lambda_function  # noqa: E402
from utils.settings_store import defaults  # noqa: E402
from utils.storage import SqliteStore  # noqa: E402

TODAY = "2026-11-18"


class Trades:
    def __init__(self, positions):
        self.positions = positions

    def open_positions(self, _account_id=None):
        return self.positions


class Telegram:
    is_configured = True
    admin_chat_ids = {"1", "2"}

    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text))


def pos(ticker="AAPL", stop=100.0):
    return {"ticker": ticker, "quantity": 2, "averageCost": 110.0, "costBasis": 220.0, "stopPrice": stop,
            "targetPrice": None}


@pytest.fixture
def notices(tmp_path):
    return SqliteStore(str(tmp_path / "s.db"))


def run(notices, positions, price, **settings):
    tg = Telegram()
    out = lambda_function.stop_watch(Trades(positions), notices, tg, {**defaults(), **settings},
                                     lambda t: price, TODAY)
    return out, tg.sent


def test_near_the_stop_warns_every_admin_once(notices):
    out, sent = run(notices, [pos()], 100.9)
    assert out == ["AAPL"]
    assert sent == [("1", "AAPL is near its stop $100.00: now $100.90 (MOCK position)."),
                    ("2", "AAPL is near its stop $100.00: now $100.90 (MOCK position).")]
    assert run(notices, [pos()], 100.5) == ([], [])  # same day: no repeat


def test_no_repeat_after_a_restart(notices, tmp_path):
    run(notices, [pos()], 100.9)
    assert run(SqliteStore(notices.path), [pos()], 100.9) == ([], [])


def test_below_the_stop_says_below(notices):
    _, sent = run(notices, [pos()], 98.0)
    assert sent[0][1] == "AAPL is below its stop $100.00: now $98.00 (MOCK position)."


def test_far_from_the_stop_no_stop_or_no_price_sends_nothing(notices):
    assert run(notices, [pos()], 101.5) == ([], [])  # more than 1% above
    assert run(notices, [pos(stop=None)], 50.0) == ([], [])
    assert run(notices, [pos()], None) == ([], [])


def test_wider_distance_setting(notices):
    out, _ = run(notices, [pos()], 104.0, stopAlertPct=5)
    assert out == ["AAPL"]


def test_telegram_alerts_off_sends_nothing(notices):
    assert run(notices, [pos()], 100.5, telegramAlertsEnabled=False) == ([], [])


def test_no_warning_on_a_weekend_or_holiday(notices):
    tg = Telegram()
    out = lambda_function.stop_watch(Trades([pos()]), notices, tg, defaults(), lambda t: 100.5, "2026-11-21")
    assert out == [] and tg.sent == []
