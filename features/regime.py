"""Market regime from SPY on a decision day: trend, volatility and character, with the inputs shown."""
from indicators import adx, hist_vol, rolling_percentile, sma

SLOPE_BARS = 20
PERCENTILE_BARS = 252


def regime(spy, as_of=None) -> dict:
    """{"asOf", "trend": bull|bear|sideways|None, "volatility": high|normal|low|None,
    "character": trending|mean_reverting|mixed|None, "inputs": {...}}. SPY bars after `as_of` are ignored.
    """
    bars = [b for b in spy if as_of is None or b.date <= as_of]
    out = {"asOf": bars[-1].date if bars else None, "trend": None, "volatility": None, "character": None,
           "inputs": {}}
    if not bars:
        return out
    close = [b.close for b in bars]
    s200 = sma(close, 200)
    if len(close) >= 200 + SLOPE_BARS:
        rising = s200[-1] > s200[-1 - SLOPE_BARS]
        above = close[-1] > s200[-1]
        out["trend"] = "bull" if above and rising else "bear" if not above and not rising else "sideways"
        out["inputs"].update(close=close[-1], sma200=s200[-1], sma200TwentyBarsAgo=s200[-1 - SLOPE_BARS])
    hv = hist_vol(close, 20) if len(close) > 1 else []
    pct = rolling_percentile(hv, PERCENTILE_BARS)[-1] if hv else None
    if pct is not None:
        out["volatility"] = "high" if pct > 80 else "low" if pct < 20 else "normal"
        out["inputs"].update(hv20=hv[-1], hv20Percentile=pct)
    a = adx([b.high for b in bars], [b.low for b in bars], close, 14)["adx"][-1]
    if a is not None:
        out["character"] = "trending" if a >= 25 else "mean_reverting" if a < 20 else "mixed"
        out["inputs"].update(adx14=a)
    return out
