"""R7: weekly bars from daily bars (completed weeks only), weekly trend, and timeframe alignment."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from features.build import alignment, build_features, timeframes, trend_state  # noqa: E402
from market_data.bars import Bar  # noqa: E402
from market_data.weekly import weekly_bars  # noqa: E402
from scoring import components as c  # noqa: E402
from scoring.aggregate import aggregate  # noqa: E402
from scoring.profiles import SWING  # noqa: E402


def day(d, close, volume=100.0):
    return Bar(d, close + 1, close - 1, close, close - 0.5, volume, close)


def trading_days(start, end):
    from market_data.calendar import is_trading_day

    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_a_week_is_built_from_its_days():
    days = [day(d, 10 + i) for i, d in enumerate(trading_days("2026-11-09", "2026-11-13"))]  # Mon to Fri
    [w] = weekly_bars(days)
    assert w == Bar("2026-11-13", 15.0, 9.0, 14.0, 9.5, 500.0, 14.0)


def test_a_midweek_decision_leaves_the_current_week_out():
    days = [day(d, 10) for d in trading_days("2026-11-09", "2026-11-18")]  # through Wednesday
    assert [w.date for w in weekly_bars(days)] == ["2026-11-13"]


def test_thanksgiving_week_ends_only_after_fridays_half_day():
    thursday_gone = trading_days("2026-11-23", "2026-11-25")  # Mon to Wed; Thu 26th is a holiday
    assert weekly_bars([day(d, 10) for d in thursday_gone]) == []
    full = [day(d, 10) for d in trading_days("2026-11-23", "2026-11-27")]
    assert [w.date for w in weekly_bars(full)] == ["2026-11-27"]


def test_a_holiday_friday_ends_the_week_on_thursday():
    days = [day(d, 10) for d in trading_days("2026-03-30", "2026-04-02")]  # Good Friday 2026-04-03 closed
    assert [w.date for w in weekly_bars(days)] == ["2026-04-02"]


def test_a_missing_volume_makes_that_weeks_volume_missing_only():
    days = [day(d, 10) for d in trading_days("2026-11-02", "2026-11-13")]
    days[7] = days[7]._replace(volume=None)  # in the second week
    first, second = weekly_bars(days)
    assert first.volume == 500.0 and second.volume is None and second.close == 10


@pytest.mark.parametrize("close, short, long, state", [
    (11, 10.5, 10, "up"), (9, 9.5, 10, "down"), (11, 9.5, 10, "flat"), (9, 10.5, 10, "flat"), (11, None, 10, None),
])
def test_trend_state(close, short, long, state):
    assert trend_state(close, short, long) == state


@pytest.mark.parametrize("daily, weekly, label", [
    ("up", "up", "strong"), ("up", "flat", "partial"), ("flat", "up", "partial"), ("up", "down", "conflicting"),
    ("down", "up", "conflicting"), ("flat", "flat", "neutral"), ("down", "down", "neutral"), ("up", None, None),
])
def test_alignment(daily, weekly, label):
    assert alignment(daily, weekly) == label


def rising(n):
    days = trading_days("2021-01-04", "2026-11-13")[-n:]
    return [day(d, 100 + i * 0.2) for i, d in enumerate(days)]


def test_a_steady_rise_is_strong_on_both_timeframes():
    f = build_features(rising(500))
    tf = f["timeframes"]
    assert tf["daily"]["trend"] == "up" and tf["weekly"]["trend"] == "up" and tf["alignment"] == "strong"
    assert tf["weekly"]["rsi14"] == 100 and tf["weekly"]["return13Pct"] > 0 and tf["weekly"]["asOf"] == "2026-11-13"
    assert c.alignment(f) == (100, "strong: daily up, weekly up")


def test_thirty_weeks_of_history_is_unknown_not_an_error():
    tf = timeframes(rising(150), None)
    assert tf["weekly"]["trend"] is None and tf["alignment"] is None
    assert c.alignment({"timeframes": tf}) == (None, "not enough weekly history")


def test_alignment_is_shown_but_never_changes_the_score():
    comps = {"trend": (80, "x"), "alignment": (0, "conflicting")}
    with_it, without = aggregate(comps, SWING), aggregate({"trend": (80, "x")}, SWING)
    assert with_it["score"] == without["score"] and with_it["coverage"] == without["coverage"]
    assert all(r["component"] != "alignment" for r in with_it["breakdown"])


def test_weekly_values_never_see_later_days():
    bars = rising(500)
    t = 400
    assert build_features(bars[:t + 1])["timeframes"] == timeframes(bars[:t + 1], build_features(bars[:t + 1])["timeframes"]["daily"]["trend"])
    later = bars[:t + 1] + [b._replace(close=b.close * 3) for b in bars[t + 1:]]
    assert build_features(later[:t + 1])["timeframes"] == build_features(bars[:t + 1])["timeframes"]


def test_analysis_route_fills_the_weekly_timeframe(monkeypatch):
    from api import main as api_main
    from config import Config
    from fastapi.testclient import TestClient

    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    tf = {"daily": {"trend": "up"}, "weekly": {"trend": "flat", "rsi14": 61.0, "return13Pct": 4.0, "weeks": 100,
                                               "asOf": "2026-11-13"}, "alignment": "partial"}
    o = {"ticker": "KO", "asOf": "2026-11-17", "qualityScore": 70, "direction": "bullish", "eligible": True,
         "breakdown": [], "strategies": {}, "risk": {"riskReward": 2.0, "planSource": "trend"}, "dataQuality": "valid",
         "coverage": 1.0, "explanation": {"positives": [], "negatives": [], "warnings": [], "gatesFailed": []},
         "versions": {"features": "2", "strategies": {}, "score": "1"}, "timeframes": tf}
    monkeypatch.setattr(api_main, "_opportunities", lambda: [o])
    body = TestClient(api_main.create_app()).get("/api/v1/analysis/KO").json()
    assert body["timeframes"]["1w"] == {"trend": "neutral", "momentum": "positive", "score": None}
    assert body["timeframes"]["alignment"] == "partial"


def test_study_labels_match_the_live_timeframes_on_every_day():
    from backtesting.alignment_study import labels_by_date
    from indicators import sma

    days = trading_days("2026-01-02", "2026-11-13")
    import math
    bars = [day(d, 100 + i * 0.1 + 6 * math.sin(i / 9)) for i, d in enumerate(days)]
    labels = labels_by_date(bars)
    close = [b.close for b in bars]
    s50, s200 = sma(close, 50), sma(close, 200)
    for t in range(len(bars)):
        live = timeframes(bars[:t + 1], trend_state(close[t], s50[t], s200[t]))["alignment"]
        assert labels[bars[t].date] == live, bars[t].date
    assert {v for v in labels.values()} - {None}  # some days carry a label
