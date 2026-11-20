"""Tests for the pure helpers that shape market data for the dashboard.

Network calls are not exercised here; these cover the arithmetic and the
classification, which is where a wrong answer would quietly mislabel a trade
setup on the card rather than failing loudly.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.market_source import (  # noqa: E402
    compute_rsi,
    market_cap_category,
    market_status,
    split_pros_cons,
)


# ── Market cap tiers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cap,expected",
    [
        (3_000_000_000_000, "MEGA"),
        (200_000_000_000, "MEGA"),
        (199_000_000_000, "LARGE"),
        (10_000_000_000, "LARGE"),
        (9_000_000_000, "MID"),
        (2_000_000_000, "MID"),
        (1_000_000_000, "SMALL"),
        (0, None),
        (None, None),
    ],
)
def test_market_cap_tiers(cap, expected):
    assert market_cap_category(cap) == expected


# ── RSI ───────────────────────────────────────────────────────────────────────

def test_rsi_needs_more_closes_than_its_period():
    # Returning a neutral 50 here would put a contract on the dashboard
    # claiming a momentum reading that was never measured.
    assert compute_rsi([1, 2, 3]) is None
    assert compute_rsi([], period=14) is None


def test_rsi_of_a_monotonic_rise_is_100():
    assert compute_rsi(list(range(1, 40))) == 100.0


def test_rsi_of_a_monotonic_fall_is_low():
    assert compute_rsi(list(range(40, 1, -1))) < 1


def test_rsi_sits_in_range_for_a_mixed_series():
    closes = [10, 11, 10.5, 12, 11.8, 13, 12.4, 13.5, 13.1, 14,
              13.6, 15, 14.2, 15.5, 15.1, 16, 15.4, 17]
    rsi = compute_rsi(closes)
    assert 0 < rsi < 100


# ── Pros / cons parsing ───────────────────────────────────────────────────────

def test_splits_a_well_formed_pros_cons_block():
    pros, cons, summary = split_pros_cons(
        "Here is the take.\nPros\n- Demand is strong\n- Margins expanding\nCons\n- Valuation rich"
    )
    assert pros == ["Demand is strong", "Margins expanding"]
    assert cons == ["Valuation rich"]
    assert summary == "Here is the take."


def test_handles_bullish_bearish_headings():
    pros, cons, _ = split_pros_cons("Bullish\n* Breakout confirmed\nBearish\n* Thin volume")
    assert pros == ["Breakout confirmed"]
    assert cons == ["Thin volume"]


def test_numbered_bullets_are_stripped():
    pros, _, _ = split_pros_cons("Pros\n1. First point\n2) Second point\nCons\n- Risk")
    assert pros == ["First point", "Second point"]


def test_unparseable_analysis_is_kept_as_the_summary():
    # An unstructured analysis is still the most useful thing on the card, so
    # it survives in `summary` rather than being dropped.
    pros, cons, summary = split_pros_cons("The model just rambled without headings.")
    assert pros == [] and cons == []
    assert summary == "The model just rambled without headings."


def test_empty_analysis_is_empty_everywhere():
    assert split_pros_cons("") == ([], [], "")
    assert split_pros_cons(None) == ([], [], "")


# ── Session labelling ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc), "PRE_MARKET"),     # Monday
        (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc), "REGULAR_HOURS"),
        (datetime(2026, 11, 16, 22, 0, tzinfo=timezone.utc), "POST_MARKET"),
        (datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc), "CLOSED"),
        (datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc), "CLOSED"),         # Saturday
        (datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc), "CLOSED"),         # Sunday
    ],
)
def test_market_status_from_utc_clock(when, expected):
    assert market_status(when) == expected


def test_regular_hours_boundaries_are_inclusive_at_the_open():
    assert market_status(datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)) == "REGULAR_HOURS"
    # 21:00 UTC is the close, so it belongs to post-market, not regular hours.
    assert market_status(datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)) == "POST_MARKET"


# -- Extended-hours price ------------------------------------------------------

class _FakeTicker:
    def __init__(self, info):
        self.info = info

    def history(self, **kwargs):
        return None


@pytest.mark.parametrize("session, info, expected", [
    (None, {"preMarketPrice": 151.0, "preMarketTime": 200, "postMarketPrice": 149.0, "postMarketTime": 100}, 151.0),
    (None, {"preMarketPrice": 151.0, "preMarketTime": 100, "postMarketPrice": 149.0, "postMarketTime": 200}, 149.0),
    (None, {"postMarketPrice": 149.5, "postMarketTime": 60}, 149.5),
    (None, {}, None),
    (0.5, {"preMarketPrice": 151.0, "preMarketTime": 200}, None),
    # after the close Yahoo can still report this morning's pre-market: older than the close, so not used
    (None, {"regularMarketTime": 150, "preMarketPrice": 151.0, "preMarketTime": 100}, None),
    (None, {"regularMarketTime": 150, "preMarketPrice": 151.0, "preMarketTime": 100,
            "postMarketPrice": 149.0, "postMarketTime": 160}, 149.0),
    (None, {"regularMarketTime": 150, "postMarketPrice": 149.0}, None),  # no time: cannot be shown newer
])
def test_quote_carries_the_newest_extended_price_only_when_closed(monkeypatch, session, info, expected):
    from api import market_source

    class _Yf:
        @staticmethod
        def Ticker(symbol):
            return _FakeTicker({"currentPrice": 150.0, "previousClose": 148.0, **info})

    monkeypatch.setattr(market_source, "_yfinance", lambda: _Yf)
    monkeypatch.setattr(market_source, "session_fraction", lambda: session)
    q = market_source._fetch_quote("AAPL")
    assert q["price"] == 150.0 and q["extendedPrice"] == expected


def test_futures_rows_are_reused_for_a_minute_and_failures_retried(monkeypatch):
    from api import market_source

    calls = []
    monkeypatch.setattr(market_source, "_cache", {})
    monkeypatch.setattr(market_source, "_futures_contract",
                        lambda symbol, name, category: calls.append(symbol) or ({"symbol": symbol} if symbol != "BAD=F" else None))
    assert market_source.futures_contract("ES=F", "S&P", "Index") == {"symbol": "ES=F"}
    assert market_source.futures_contract("ES=F", "S&P", "Index") == {"symbol": "ES=F"}
    assert market_source.futures_contract("BAD=F", "x", "y") is None
    assert market_source.futures_contract("BAD=F", "x", "y") is None
    assert calls == ["ES=F", "BAD=F", "BAD=F"]  # one Yahoo call for ES=F; the failure is retried


# ── Real LLM shapes (markdown) that used to break the split ─────────────────

def test_markdown_write_up_with_a_bare_bullish_inside_a_con_still_splits():
    # "No bullish MACD cross" once moved the split point into the cons, and the card showed one grey paragraph.
    text = ("**Pros**  \n- **Strong up-trend:** Price is well above the 200-day SMA.  \n- **Unusual volume:** 3x average.\n"
            "**Cons**  \n- **MACD weakness:** No bullish MACD cross in the past three days.  \n"
            "- **Risk:** The stop is 15% below entry.\n*Overall, the momentum and risk undermine the trade.*")
    pros, cons, summary = split_pros_cons(text)
    assert pros == ["Strong up-trend: Price is well above the 200-day SMA.", "Unusual volume: 3x average."]
    assert cons == ["MACD weakness: No bullish MACD cross in the past three days.", "Risk: The stop is 15% below entry."]
    assert summary == "Overall, the momentum and risk undermine the trade."


def test_words_containing_pros_or_cons_are_not_headings():
    pros, cons, _ = split_pros_cons("Pros:\n- Tight consolidation before the move\n- Good prospects\nCons:\n- Thin float")
    assert pros == ["Tight consolidation before the move", "Good prospects"]
    assert cons == ["Thin float"]


def test_bullets_run_together_on_one_line_and_empty_fragments_are_dropped():
    pros, cons, _ = split_pros_cons("**Pros** - **Trend:** up - **Volume:** high\n**\n**Cons** - **MACD:** flat")
    assert pros == ["Trend: up", "Volume: high"]
    assert cons == ["MACD: flat"]


def test_cons_listed_first_and_a_bottom_line_verdict():
    pros, cons, summary = split_pros_cons("Cons:\n- Weak trend\nPros:\n- Volume spike\nBottom line: wait for a cross.")
    assert (pros, cons, summary) == (["Volume spike"], ["Weak trend"], "Bottom line: wait for a cross.")


def test_an_unparsed_write_up_loses_its_markdown_markers():
    assert split_pros_cons("**Note:** the model skipped the headings.") == ([], [], "Note: the model skipped the headings.")
