"""R8: current fundamentals (stored, refreshed weekly), the fundamental component and the long-term score."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data import fundamentals as fm  # noqa: E402
from scoring import components as c  # noqa: E402
from scoring.aggregate import aggregate  # noqa: E402
from scoring.profiles import LONG_TERM, SWING  # noqa: E402

# Shapes as Yahoo returned them on 2026-11-19 (test data, rounded).
KO = {"revenueGrowth": 0.074, "earningsGrowth": 0.083, "grossMargins": 0.623, "operatingMargins": 0.322,
      "profitMargins": 0.287, "returnOnEquity": 0.406, "debtToEquity": 107.4, "currentRatio": 1.377,
      "freeCashflow": 4.349e9, "trailingPE": 29.1, "forwardPE": 27.8, "priceToSalesTrailing12Months": 8.27,
      "enterpriseToEbitda": 25.8, "marketCap": 4.22e11}
BANK = {**{k: None for k in fm.FIELDS}, "revenueGrowth": 0.102, "earningsGrowth": 0.099, "grossMargins": None,
        "operatingMargins": 0.448, "profitMargins": 0.346, "returnOnEquity": 0.18, "trailingPE": 13.6, "forwardPE": 12.7,
        "priceToSalesTrailing12Months": 4.51, "marketCap": 8.6e11}
ETF = {**{k: None for k in fm.FIELDS}, "trailingPE": 23.2}
LOSSES = {**KO, "trailingPE": None, "forwardPE": -27.0, "freeCashflow": -1.96e10, "profitMargins": -0.256}


def scores(values):
    return {m["key"]: m["score"] for m in c.fundamental_metrics(values)}


def test_each_metric_is_scored_by_its_tiers():
    s = scores(KO)
    assert (s["revenueGrowth"], s["grossMargins"], s["operatingMargins"], s["returnOnEquity"]) == (50, 100, 100, 100)
    assert (s["debtToEquity"], s["currentRatio"], s["freeCashflow"]) == (50, 50, 100)
    assert (s["trailingPE"], s["priceToSales"], s["evToEbitda"], s["fcfYield"]) == (50, 0, 0, 0)  # P/S 8.3; FCF yield ~1%
    score, detail = c.fundamental(KO)
    assert score == round(sum(v for v in s.values()) / 14) and "strong Gross margin %" in detail


def test_a_bank_is_never_marked_down_for_what_yahoo_does_not_report():
    s = scores(BANK)
    assert s["grossMargins"] is None and s["debtToEquity"] is None and s["currentRatio"] is None
    score, _ = c.fundamental(BANK)
    present = [v for v in s.values() if v is not None]
    assert score == round(sum(present) / len(present)) and score >= 50


def test_an_etf_has_no_fundamental_score():
    assert c.fundamental(ETF) == (None, "only 1 fundamentals available")
    assert c.fundamental(None) == (None, "only 0 fundamentals available")


def test_losses_are_weak_but_a_missing_pe_is_missing():
    s = scores(LOSSES)
    assert s["trailingPE"] is None and s["forwardPE"] == 0 and s["freeCashflow"] == 0 and s["profitMargins"] == 0


def test_download_keeps_numbers_only_and_survives_errors(monkeypatch):
    monkeypatch.undo()  # drop the conftest stub: this test patches Yahoo itself
    real = fm.download_fundamentals

    class T:
        def __init__(self, t):
            if t == "BOOM":
                raise RuntimeError("rate limited")
            self.info = {"grossMargins": 0.0, "trailingPE": 13.6, "marketCap": True, "forwardPE": float("nan"),
                         "revenueGrowth": "n/a"}

    monkeypatch.setattr("yfinance.Ticker", T)
    v = real("JPM")
    assert v["grossMargins"] is None and v["trailingPE"] == 13.6 and v["marketCap"] is None
    assert v["forwardPE"] is None and v["revenueGrowth"] is None and set(v) == set(fm.FIELDS)
    assert real("BOOM") is None


def test_refresh_is_weekly_spread_out_and_keeps_old_values_on_failure(tmp_path, monkeypatch):
    store = fm.FundamentalsStore(str(tmp_path / "f.db"))
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    store.save("OLD", KO, old)
    store.save("NEW", KO, fresh)
    monkeypatch.setattr(fm, "PER_PASS", 2)
    assert store.due(["NEW", "OLD", "A", "B"]) == ["A", "B"]  # never fetched first, capped
    assert store.due(["NEW", "OLD"]) == ["OLD"]
    saved = fm.refresh_fundamentals(store, ["NEW", "OLD"], download=lambda t: None)  # Yahoo down
    assert saved == 0 and store.get(["OLD"])["OLD"] == {"values": KO, "fetchedAt": old}
    assert fm.refresh_fundamentals(store, ["OLD"], download=lambda t: BANK) == 1
    assert store.get(["OLD"])["OLD"]["values"] == BANK


def test_long_term_weighs_fundamentals_and_leaves_the_swing_score_alone():
    comps = {"trend": (80, "x"), "regime": (100, "bull"), "risk_reward": (50, "2.0 to 1")}
    swing_before = aggregate(comps, SWING)["score"]
    with_f = {**comps, "fundamental": c.fundamental(KO)}
    assert aggregate(with_f, SWING)["score"] == swing_before  # swing has no fundamental weight
    lt = aggregate(with_f, LONG_TERM)
    assert "fundamental" in {r["component"] for r in lt["breakdown"]}
    assert aggregate({**comps, "fundamental": c.fundamental(ETF)}, LONG_TERM)["coverage"] < lt["coverage"]


def test_opportunity_carries_the_long_term_block():
    from market_data.bars import Bar
    from scoring.opportunity import build_opportunity

    from features.build import build_features

    bars = [Bar(f"2026-11-{d:02d}", 11.0, 9.0, 10.0, 10.0, 1e7, 10.0) for d in range(2, 18)]
    features = build_features(bars)
    o = build_opportunity("KO", bars, features, {}, {"trend": "bull", "volatility": "normal"}, "valid", None,
                          date(2026, 11, 18), fundamentals={"values": KO, "fetchedAt": "2026-11-19T00:00:00+00:00"})
    lt = o["longTerm"]
    assert lt["note"] == "Current fundamentals, not point-in-time" and lt["fetchedAt"].startswith("2026-11-19")
    assert len(lt["metrics"]) == 14 and lt["score"] is not None
    none = build_opportunity("SPY", bars, features, {}, None, "valid", None, date(2026, 11, 18))
    assert none["longTerm"]["metrics"] == [] and none["longTerm"]["fetchedAt"] is None
