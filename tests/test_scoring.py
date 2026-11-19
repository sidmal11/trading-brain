"""Scoring: components, weighted aggregate (hand-computed), gates, risk plan, explanation, ranking, store."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from features.build import build_features  # noqa: E402
from risk.assess import assess, best_plan  # noqa: E402
from scoring import components as c  # noqa: E402
from scoring.aggregate import aggregate  # noqa: E402
from scoring.explain import gates  # noqa: E402
from scoring.opportunity import OpportunityStore, build_opportunity, rank  # noqa: E402
from scoring.profiles import SWING  # noqa: E402
from strategies import breakout, mean_reversion, trend  # noqa: E402
from tests.test_features import walk  # noqa: E402

TODAY = date(2026, 11, 18)


def test_aggregate_hand_computed():
    comps = {"trend": (80, ""), "momentum": (60, ""), "breakout": (40, ""), "price_action": (100, ""),
             "volume": (None, "volume missing"), "relative_strength": (50, ""), "regime": (100, ""),
             "risk_reward": (50, "")}
    out = aggregate(comps, SWING)
    # trend family 15 + 12 + 12 = 39 capped to 35: weights x 35/39. Used total = 35 + 15 + 8 + 8 + 10 = 76.
    k = 35 / 39
    expected = (15 * k * 80 + 12 * k * 60 + 12 * k * 40 + 15 * 100 + 8 * 50 + 8 * 100 + 10 * 50) / 76
    assert out["score"] == pytest.approx(round(expected, 1)) and out["direction"] == "bullish"
    assert out["coverage"] == pytest.approx(80 / 90)  # volume missing; reliability and ML weigh 0
    assert sum(r["weight"] for r in out["breakdown"]) == pytest.approx(1)
    assert out["breakdown"][0]["component"] == "price_action"  # biggest contribution first
    assert aggregate(comps, SWING, "warn")["score"] == pytest.approx(round(expected * 0.9, 1))


def test_no_data_gives_no_score_not_zero():
    out = aggregate({"trend": (None, "")}, SWING)
    assert out["score"] is None and out["direction"] == "neutral" and out["coverage"] == 0


def test_component_rules():
    assert c.regime({"trend": "bull", "volatility": "high"})[0] == 75
    assert c.regime({"trend": "bear", "volatility": "high"})[0] == 0
    assert c.regime({"trend": None})[0] is None
    assert [c.risk_reward(x)[0] for x in (0.8, 1.0, 2.0, 3.5)] == [0, 0, 50, 100]
    assert c.risk_reward(None)[0] is None
    f = {"relativeStrength": {"vsSpy": {"20": 5.0, "60": -2.0}, "vsSector": {"20": None}, "sectorEtf": None}}
    assert c.relative_strength(f)[0] == 50  # 35 of the 70 points available
    short = trend.evaluate_series(walk(100))[-1]
    assert c.strategy_component(short)[0] is None


def test_feature_components_on_real_shaped_features():
    f = build_features(walk(500), walk(500, 9))
    for fn in (c.momentum, c.price_action, c.volume, c.relative_strength):
        score, detail = fn(f)
        assert score is None or 0 <= score <= 100
        assert isinstance(detail, str)


def risk(**over):
    base = {"dollarVolume20": 50e6, "atrPct": 2.0, "riskReward": 2.0}
    return {**base, **over}


@pytest.mark.parametrize("over, quality, coverage, newest, reason", [
    ({}, "valid", 0.9, "2026-11-10", "Stale data"),
    ({}, "invalid", 0.9, "2026-11-17", "Data quality invalid"),
    ({}, "valid", 0.6, "2026-11-17", "Only 60% of the score has data"),
    ({"dollarVolume20": 5e6}, "valid", 0.9, "2026-11-17", "Average dollar volume $5M under $20M"),
    ({"atrPct": 9.5}, "valid", 0.9, "2026-11-17", "Too volatile"),
    ({"riskReward": 1.2}, "valid", 0.9, "2026-11-17", "Reward/risk 1.20 under 1.5"),
    ({"riskReward": None}, "valid", 0.9, "2026-11-17", "No usable plan"),
])
def test_each_gate(over, quality, coverage, newest, reason):
    failed = gates(risk(**over), coverage, quality, newest, TODAY)
    assert len(failed) == 1 and failed[0].startswith(reason)


def test_clean_ticker_passes_every_gate():
    assert gates(risk(), 0.9, "valid", "2026-11-17", TODAY) == []


def test_plan_from_the_best_bullish_strategy_else_the_default():
    bars = walk(300)
    weak = {"direction": "bullish", "confidence": 60, "stopPrice": 90.0, "targetPrice": 130.0}
    strong = {"direction": "bullish", "confidence": 85, "stopPrice": 95.0, "targetPrice": 120.0}
    assert best_plan({"trend": weak, "breakout": strong})[0] == "breakout"
    r = assess(bars, {"trend": {"direction": "neutral", "confidence": 30}})
    assert r["planSource"] == "default" and r["riskReward"] == pytest.approx(2.0)
    assert r["stop"] == pytest.approx(r["entry"] - 1.5 * r["atr"])


def test_build_opportunity_end_to_end():
    bars, spy = walk(500), walk(500, 9)
    results = {m.NAME: m.evaluate_series(bars, spy)[-1] for m in (trend, breakout, mean_reversion)}
    o = build_opportunity("AAA", bars, build_features(bars, spy), results, {"trend": "bull", "volatility": "normal"},
                          "valid", date(2026, 11, 19), TODAY)
    assert o["calibratedProbability"] is None and o["bearishScore"] is None
    assert o["eligible"] is False and o["explanation"]["gatesFailed"][0].startswith("Stale data")  # 2024-25 bars
    assert o["explanation"]["warnings"] == ["Earnings in 1 day (Nov 19)"]
    assert o["versions"]["score"] == "1" and o["versions"]["model"] is None
    assert set(o["strategies"]) == {"trend", "breakout", "mean_reversion"}
    assert len(o["explanation"]["topContributions"]) == 3


def test_rank_puts_eligible_first_and_missing_scores_last():
    rows = [{"eligible": False, "qualityScore": 90}, {"eligible": True, "qualityScore": 55},
            {"eligible": True, "qualityScore": None}, {"eligible": True, "qualityScore": 70}]
    assert [(o["eligible"], o["qualityScore"]) for o in rank(rows)] == [(True, 70), (True, 55), (True, None), (False, 90)]


def test_store_latest_and_get_with_a_dashed_ticker(tmp_path):
    store = OpportunityStore(str(tmp_path / "s.db"))
    store.save([{"id": "BRK-B-2026-11-17", "ticker": "BRK-B", "asOf": "2026-11-17", "eligible": True, "qualityScore": 60},
                {"id": "AAPL-2026-11-16", "ticker": "AAPL", "asOf": "2026-11-16", "eligible": True, "qualityScore": 99}])
    assert [o["ticker"] for o in store.latest()] == ["BRK-B"]  # the newest day only
    assert store.get("BRK-B-2026-11-17")["ticker"] == "BRK-B" and store.get("ZZZ-2026-11-17") is None


def test_component_detail_says_what_it_has_and_lacks():
    f = {"relativeStrength": {"vsSpy": {"20": 5.0, "60": -2.0}, "vsSector": {"20": None}, "sectorEtf": None}}
    assert c.relative_strength(f)[1] == "has +5.0 points vs SPY over 20 days; lacks -2.0 vs SPY over 60 days"
