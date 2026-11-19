"""Strategy reliability: a t-statistic on holdout returns, no score under 30 trades, weight 0 (shown only)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring.aggregate import aggregate  # noqa: E402
from scoring.profiles import LONG_TERM, SWING  # noqa: E402
from scoring.reliability import MIN_TRADES, reliability, strategy_reliability  # noqa: E402


def test_too_few_trades_get_no_score_however_good():
    assert reliability([0.05] * (MIN_TRADES - 1)) == (None, "only 29 holdout trades (needs 30)")


def test_score_is_50_plus_25_t_and_clamped():
    returns = [0.02, -0.01] * 20  # mean 0.5%, sd ~1.52%, n 40
    mean, sd = 0.005, math.sqrt(sum((r - 0.005) ** 2 for r in returns) / 39)
    t = mean / (sd / math.sqrt(40))
    score, detail = reliability(returns)
    assert score == round(min(50 + 25 * t, 100)) and detail.startswith("40 holdout trades, +0.50% each")
    assert reliability([0.03, 0.02] * 30)[0] == 100
    assert reliability([-0.03, -0.02] * 30)[0] == 0
    assert reliability([0.01] * 40) == (None, "holdout returns do not vary")


def test_a_short_lucky_run_scores_lower_than_a_long_steady_one():
    lucky = reliability([0.04, -0.02] * 16)[0]  # 32 trades, big mean
    steady = reliability([0.02, -0.01] * 150)[0]  # 300 trades, smaller mean
    assert steady > lucky


class FakeResearch:
    def __init__(self, runs):
        self.runs = runs

    def latest_run(self, name):
        return self.runs.get(name) and {"id": name, "result": {"holdout": {"from": "2025-11-17"}}}

    def run_trades(self, run_id, limit=500):
        return self.runs[run_id]


def test_strategy_reliability_reads_only_holdout_trades():
    trades = [{"signal_date": "2024-01-02", "ret": -0.5}] * 50 + [{"signal_date": "2026-01-02", "ret": r} for r in [0.02, -0.01] * 20]
    out = strategy_reliability(FakeResearch({"trend": trades}), ["trend", "squeeze"])
    assert out["trend"]["trades"] == 40 and out["trend"]["score"] > 50
    assert out["squeeze"] == {"score": None, "detail": "no backtest yet", "trades": 0}


def test_reliability_never_changes_a_score():
    comps = {"trend": (80, "x")}
    for profile in (SWING, LONG_TERM):
        assert profile["reliability"] == 0
        assert aggregate({**comps, "reliability": (100, "x")}, profile) == aggregate(comps, profile)
