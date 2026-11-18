"""Momentum: RSI, MACD, Stochastic RSI, rate of change, momentum."""
from indicators import _check
from indicators.trend import ema


def macd(closes, fast=12, slow=26, signal=9):
    """(line, signal, histogram): EMA(fast) - EMA(slow), EMA(signal) of that, and their difference."""
    line = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    sig = ema(line, signal)
    return line, sig, [m - s for m, s in zip(line, sig)]


def macd_hist_slope(closes, fast=12, slow=26, signal=9):
    """Bar-to-bar change of the MACD histogram. Warm-up: 1."""
    hist = macd(closes, fast, slow, signal)[2]
    return [None] + [b - a for a, b in zip(hist, hist[1:])]


def rsi(closes, n=14):
    """Wilder's RSI, 0 to 100. Warm-up: n (the first value needs n changes).

    The first average gain and loss are the plain means of the first n changes, then Wilder smoothing.
    No losses gives 100; no gains and no losses (flat) gives 50.
    """
    _check.period(n)
    length = _check.series(closes)
    out = [None] * length
    if length <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, length)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, length)]
    avg_gain, avg_loss = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, length):
        if i > n:
            avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        if avg_loss == 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            out[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def stoch_rsi(closes, n=14):
    """Where RSI sits in its own range over the last n RSI values, 0 to 100. Warm-up: 2n - 1.

    None when the RSI range is zero (no movement to place it in).
    """
    values = rsi(closes, n)
    out = [None] * len(values)
    for i in range(len(values)):
        window = values[i - n + 1:i + 1] if i >= n - 1 else []
        if len(window) < n or None in window:
            continue
        lo, hi = min(window), max(window)
        out[i] = None if hi == lo else 100 * (values[i] - lo) / (hi - lo)
    return out


def roc(closes, n=12):
    """Rate of change in percent: (c[i] / c[i - n] - 1) * 100. Warm-up: n."""
    _check.period(n)
    _check.series(closes)
    return [None if i < n or closes[i - n] == 0 else (closes[i] / closes[i - n] - 1) * 100
            for i in range(len(closes))]


def momentum(closes, n=10):
    """Price change over n bars: c[i] - c[i - n]. Warm-up: n."""
    _check.period(n)
    _check.series(closes)
    return [None if i < n else closes[i] - closes[i - n] for i in range(len(closes))]
