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
        (0, "SMALL"),
        (None, "SMALL"),
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
