"""Opt-in research notes (owner Q17): high scores and agreement among the four approved strategies, once a day."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lambda_function  # noqa: E402
from config import Config  # noqa: E402
from scoring import components as c  # noqa: E402
from scoring.opportunity import OpportunityStore  # noqa: E402


class Tg:
    is_configured = True
    admin_chat_ids = {"1"}

    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text):
        self.sent.append(text)


def opportunity(ticker, score, strategies):
    return {"ticker": ticker, "asOf": "2026-11-17", "eligible": True, "direction": "bullish", "qualityScore": score,
            "strategies": {k: {"score": s, "direction": "neutral"} for k, s in strategies.items()},
            "explanation": {"positives": ["Trend 80"]}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "r.db"))
    return OpportunityStore(Config.SQLITE_PATH)


def test_agreement_counts_only_the_four_approved_strategies(env):
    env.save([opportunity("NEW", 55, {"momentum": 90, "pullback": 80, "macd": 61}),  # one approved strategy at 60+
              opportunity("OLD", 55, {"macd": 70, "breakout": 65, "momentum": 10})])  # two approved
    tg = Tg()
    lambda_function._research_alerts({"agreementAlertsEnabled": True}, tg, "2026-11-18")
    assert tg.sent == ["OLD: 2 strategies score 60 or more (breakout, macd)."]


def test_each_note_goes_once_a_day(env):
    env.save([opportunity("KO", 80, {})])
    tg, settings = Tg(), {"scoreAlertsEnabled": True, "scoreAlertMin": 75}
    lambda_function._research_alerts(settings, tg, "2026-11-18")
    lambda_function._research_alerts(settings, tg, "2026-11-18")
    assert tg.sent == ["KO research score 80 (not a probability): Trend 80."]


def test_latest_for_reads_one_ticker(env):
    env.save([{**opportunity("KO", 70, {}), "asOf": "2026-11-16"}, opportunity("KO", 71, {}), opportunity("PG", 60, {})])
    assert env.latest_for("KO")["qualityScore"] == 71 and env.latest_for("XYZ") is None


def test_negative_debt_to_equity_is_weak_not_healthy():
    scores = {m["key"]: m["score"] for m in c.fundamental_metrics({"debtToEquity": -350.0})}
    assert scores["debtToEquity"] == 0
    assert {m["key"]: m["score"] for m in c.fundamental_metrics({"debtToEquity": 80.0})}["debtToEquity"] == 100
