"""Volume: relative volume, on-balance volume, money flow index, Chaikin money flow."""
from indicators import _check


def relative_volume(volume, n=20):
    """This bar's volume over the average of the n bars before it. Warm-up: n. None when that average is 0."""
    _check.period(n)
    _check.series(volume)
    out = [None] * len(volume)
    for i in range(n, len(volume)):
        avg = sum(volume[i - n:i]) / n
        out[i] = None if avg == 0 else volume[i] / avg
    return out


def obv(close, volume):
    """On-balance volume: starts at 0; adds the bar's volume on an up close, subtracts it on a down close."""
    length = _check.series(close, volume)
    out, total = [], 0.0
    for i in range(length):
        if i > 0:
            total += volume[i] if close[i] > close[i - 1] else -volume[i] if close[i] < close[i - 1] else 0.0
        out.append(total)
    return out


def mfi(high, low, close, volume, n=14):
    """Money flow index, 0 to 100, over the last n bars. Warm-up: n.

    Typical price = (high + low + close) / 3; money flow = typical price x volume, counted positive when the
    typical price rose from the bar before and negative when it fell. No negative flow gives 100; no flow gives 50.
    """
    _check.period(n)
    length = _check.series(high, low, close, volume)
    tp = [(h + lo + c) / 3 for h, lo, c in zip(high, low, close)]
    pos = [0.0] + [tp[i] * volume[i] if tp[i] > tp[i - 1] else 0.0 for i in range(1, length)]
    neg = [0.0] + [tp[i] * volume[i] if tp[i] < tp[i - 1] else 0.0 for i in range(1, length)]
    out = [None] * length
    for i in range(n, length):
        p, q = sum(pos[i - n + 1:i + 1]), sum(neg[i - n + 1:i + 1])
        out[i] = (100.0 if p > 0 else 50.0) if q == 0 else 100 - 100 / (1 + p / q)
    return out


def cmf(high, low, close, volume, n=20):
    """Chaikin money flow over the last n bars, -1 to 1. Warm-up: n - 1. None when those bars traded nothing.

    Multiplier = ((close - low) - (high - close)) / (high - low), 0 on a bar with no range.
    """
    _check.period(n)
    length = _check.series(high, low, close, volume)
    mfv = [0.0 if h == lo else ((c - lo) - (h - c)) / (h - lo) * v
           for h, lo, c, v in zip(high, low, close, volume)]
    out = [None] * length
    for i in range(n - 1, length):
        total = sum(volume[i - n + 1:i + 1])
        out[i] = None if total == 0 else sum(mfv[i - n + 1:i + 1]) / total
    return out
