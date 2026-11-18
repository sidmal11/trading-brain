"""Request validity and order sizing. Pure: no I/O."""

from decimal import ROUND_DOWN, Decimal


def _down(x: float) -> float:
    return float(Decimal(repr(x)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))


def invalid_reason(alert: dict, price: float | None, settings: dict) -> str | None:
    if price is None:
        return None
    stop = alert.get("stopPrice")
    if stop is not None and price <= stop:
        return f"Price fell to the stop ${stop:.2f}."
    at = alert.get("priceAtAlert")
    pct = settings["maxChasePct"]
    if at is not None and price >= at * (1 + pct / 100):
        return f"Price ran more than {pct:g}% above the alert price ${at:.2f}."
    return None


def suggest_quantity(price: float | None, stop: float | None, settings: dict) -> float:
    if price is None or price <= 0:
        return 0.0
    cap = settings["dollarsPerTrade"] / price
    if stop is not None and 0 < stop < price:
        return _down(min(settings["riskPerTrade"] / (price - stop), cap))
    return _down(cap)


def quantity_for(price: float, shares: float | None, dollars: float | None, suggested: float) -> float:
    if shares is not None:
        return _down(shares)
    if dollars is not None:
        return _down(dollars / price)
    return _down(suggested)


def format_qty(q: float) -> str:
    return f"{q:.6f}".rstrip("0").rstrip(".")
