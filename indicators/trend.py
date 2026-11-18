"""Trend: moving averages, slope, ADX with +DI and -DI."""
from indicators import _check
from indicators._check import wilder


def sma(values, n):
    """Simple moving average of the last n values. Warm-up: n - 1. A running sum (fast over long history)."""
    _check.period(n)
    _check.series(values)
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values, n):
    """Exponential moving average, alpha = 2 / (n + 1), seeded with the first value (pandas ewm, adjust=False).

    No None warm-up, but the first ~3n values still lean on the seed; the strategies read 500 bars.
    """
    _check.period(n)
    _check.series(values)
    alpha = 2 / (n + 1)
    out = []
    for v in values:
        out.append(v if not out else alpha * v + (1 - alpha) * out[-1])
    return out


def slope(values, n):
    """Average change per bar over the last n bars: (v[i] - v[i - n]) / n. Warm-up: n. None inputs give None."""
    _check.period(n)
    return [None if i < n or values[i] is None or values[i - n] is None else (values[i] - values[i - n]) / n
            for i in range(len(values))]


def adx(high, low, close, n=14):
    """Wilder's directional movement. Returns {"plus_di", "minus_di", "adx"}.

    +DM = up move when it beats the down move and is positive (else 0); -DM likewise. TR on bar 0 is
    high - low. DI = 100 * Wilder(DM) / Wilder(TR), from bar n. DX = 100 * |+DI - -DI| / (+DI + -DI)
    (0 when both are 0); ADX = Wilder(DX), from bar 2n - 1.
    """
    _check.period(n)
    length = _check.series(high, low, close)
    plus_dm, minus_dm = [None] * length, [None] * length
    for i in range(1, length):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    from indicators.volatility import true_range

    tr = true_range(high, low, close)
    tr_s = wilder([None] + tr[1:], n)  # same bars as the DMs, so the ratios line up
    plus_s, minus_s = wilder(plus_dm, n), wilder(minus_dm, n)
    plus_di, minus_di, dx = [None] * length, [None] * length, [None] * length
    for i in range(length):
        if tr_s[i] is None:
            continue
        plus_di[i] = 100 * plus_s[i] / tr_s[i] if tr_s[i] else 0.0
        minus_di[i] = 100 * minus_s[i] / tr_s[i] if tr_s[i] else 0.0
        total = plus_di[i] + minus_di[i]
        dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / total if total else 0.0
    return {"plus_di": plus_di, "minus_di": minus_di, "adx": wilder(dx, n)}
