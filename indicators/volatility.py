"""Volatility: true range, ATR, Bollinger and Keltner bands, historical volatility, average daily range."""
import math

from indicators import _check
from indicators._check import wilder
from indicators.trend import ema, sma


def true_range(high, low, close):
    """max(high - low, |high - previous close|, |low - previous close|); bar 0 is high - low."""
    length = _check.series(high, low, close)
    return [high[i] - low[i] if i == 0 else
            max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
            for i in range(length)]


def atr(high, low, close, n=14):
    """Wilder's average true range. Warm-up: n - 1 (the first value is the mean of the first n true ranges)."""
    _check.period(n)
    return wilder(true_range(high, low, close), n)


def atr_pct(high, low, close, n=14):
    """ATR as a percent of the close."""
    return [None if a is None or c == 0 else 100 * a / c for a, c in zip(atr(high, low, close, n), close)]


def rolling_percentile(values, n):
    """Percent of the last n values (current included) at or below the current one, 0 to 100.

    Warm-up: n numbers; None inputs (another indicator's warm-up) give None until n numbers are available.
    """
    _check.period(n)
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        if None in window:
            continue
        out[i] = 100 * sum(v <= values[i] for v in window) / n
    return out


def _stdev(values, n, ddof):
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        mean = sum(window) / n
        out[i] = math.sqrt(sum((v - mean) ** 2 for v in window) / (n - ddof))
    return out


def bollinger(closes, n=20, k=2.0):
    """Bollinger bands on the population standard deviation. Warm-up: n - 1.

    Returns {"middle", "upper", "lower", "bandwidth" (percent of the middle), "percent_b" (0 at the lower band,
    1 at the upper; None when the bands touch)}.
    """
    _check.period(n)
    _check.series(closes)
    middle, sd = sma(closes, n), _stdev(closes, n, ddof=0)
    upper = [None if m is None else m + k * s for m, s in zip(middle, sd)]
    lower = [None if m is None else m - k * s for m, s in zip(middle, sd)]
    bandwidth = [None if m in (None, 0) else 100 * (u - lo) / m for m, u, lo in zip(middle, upper, lower)]
    percent_b = [None if u is None or u == lo else (c - lo) / (u - lo) for c, u, lo in zip(closes, upper, lower)]
    return {"middle": middle, "upper": upper, "lower": lower, "bandwidth": bandwidth, "percent_b": percent_b}


def keltner(high, low, close, n=20, mult=2.0, atr_n=10):
    """Keltner channel: EMA(n) of the close +/- mult x ATR(atr_n). Returns {"middle", "upper", "lower"}."""
    middle, band = ema(close, n), atr(high, low, close, atr_n)
    return {"middle": middle,
            "upper": [None if a is None else m + mult * a for m, a in zip(middle, band)],
            "lower": [None if a is None else m - mult * a for m, a in zip(middle, band)]}


def hist_vol(closes, n=20):
    """Annualised historical volatility in percent: sample stdev of the last n log returns x sqrt(252). Warm-up: n."""
    _check.period(n)
    _check.series(closes)
    if any(c <= 0 for c in closes):
        raise ValueError("historical volatility needs positive prices")
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if n < 2:
        raise ValueError("historical volatility needs n of at least 2")
    sd = _stdev(returns, n, ddof=1)
    return [None] + [None if s is None else 100 * s * math.sqrt(252) for s in sd]


def adr(high, low, n=20):
    """Average daily range in percent: mean of (high / low - 1) x 100 over the last n bars. Warm-up: n - 1."""
    _check.series(high, low)
    if any(lo <= 0 for lo in low):
        raise ValueError("average daily range needs positive lows")
    return sma([(h / lo - 1) * 100 for h, lo in zip(high, low)], n)
