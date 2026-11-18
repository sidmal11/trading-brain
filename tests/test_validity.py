"""Validity rules and risk-based sizing (pure functions)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.validity import format_qty, invalid_reason, quantity_for, suggest_quantity  # noqa: E402

S = {"maxChasePct": 3, "riskPerTrade": 50, "dollarsPerTrade": 500}


def test_stop_hit_at_equal_price():
    assert invalid_reason({"stopPrice": 95.0}, 95.0, S) == "Price fell to the stop $95.00."
    assert invalid_reason({"stopPrice": 95.0}, 95.01, S) is None


def test_ran_away_at_exactly_three_percent():
    a = {"priceAtAlert": 100.0}
    assert invalid_reason(a, 103.0, S) == "Price ran more than 3% above the alert price $100.00."
    assert invalid_reason(a, 102.99, S) is None


def test_missing_fields_and_price_skip():
    assert invalid_reason({}, 1.0, S) is None
    assert invalid_reason({"stopPrice": 95.0, "priceAtAlert": 100.0}, None, S) is None


def test_risk_sizing_capped_by_dollars():
    assert suggest_quantity(100.0, 95.0, {**S, "dollarsPerTrade": 5000}) == 10.0
    assert suggest_quantity(100.0, 95.0, S) == 5.0


def test_no_stop_uses_dollars_rounded_down():
    q = suggest_quantity(153.7, None, S)
    assert q == int(500 / 153.7 * 1e6) / 1e6
    assert suggest_quantity(153.7, 160.0, S) == q  # stop above price is unusable
    assert suggest_quantity(None, 95.0, S) == 0.0
    assert suggest_quantity(0, 95.0, S) == 0.0


def test_quantity_for():
    assert quantity_for(153.7, None, 10, 9.0) == 0.065061
    assert quantity_for(153.7, 2.5, 10, 9.0) == 2.5
    assert quantity_for(153.7, None, None, 3.25) == 3.25


def test_format_qty():
    assert format_qty(3.0) == "3"
    assert format_qty(0.06506100) == "0.065061"
    assert format_qty(0.0654) == "0.0654"


def test_non_positive_alert_price_skips_the_run_up_rule():
    assert invalid_reason({"priceAtAlert": 0}, 50.0, S) is None
    assert invalid_reason({"priceAtAlert": -1.0}, 50.0, S) is None
