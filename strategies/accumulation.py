"""Volume analysis, accumulation (rule sheet 5, approved 2026-11-18): steady buying pressure under a rising price.
The volume-surge strategy (volume.py) is separate and stays dashboard only. Long only.
"""
from indicators import atr, cmf, mfi, obv, relative_volume, sma
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "accumulation"
SIGNAL_TYPE = "ACCUMULATION"
MAX_HOLD = 20
ITEMS = [
    ("obv", "OBV trend over 20 days above 0.2 (more volume on up days than down days)", 25),
    ("cmf", "Chaikin money flow (20) above 0.05", 20),
    ("mfi", "Money flow index (14) between 50 and 80", 15),
    ("surge", "Relative volume at least 1.5 on an up close in the last 3 days", 20),
    ("ma", "Close above the 50-day average", 20),
]
BUY_KEYS = ("obv", "cmf", "ma")
WARMUP = 60
WINDOW = 30  # bars the volume indicators read; a missing volume among them blocks the reading


def evaluate_series(bars, spy=None):
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    vol = [b.volume for b in bars]
    a, s50 = atr(high, low, close, 14), sma(close, 50)
    out = []
    for t in range(n):
        lo_t = t - WINDOW + 1
        v = vol[lo_t:t + 1] if lo_t >= 0 else [None]
        if t < WARMUP or a[t] is None or s50[t] is None or None in v:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            if t >= WARMUP and None in v:
                for x in out[-1]["checklist"]:
                    x["detail"] = "No volume reading"
            continue
        h, lo, c = high[lo_t:t + 1], low[lo_t:t + 1], close[lo_t:t + 1]
        o, rv = obv(c, v), relative_volume(v, 20)
        f, m = cmf(h, lo, c, v, 20)[-1], mfi(h, lo, c, v, 14)[-1]
        traded = sum(v[-20:])
        trend = (o[-1] - o[-21]) / traded if traded else None  # same measure as the obvTrend20 feature
        surge = [rv[i] for i in (-3, -2, -1) if rv[i] is not None and rv[i] >= 1.5 and c[i] > c[i - 1]]
        items = [
            item("obv", ITEMS[0][1], trend is not None and trend > 0.2, 25,
                 "No volume traded" if trend is None else f"OBV trend {trend:+.2f}"),
            item("cmf", ITEMS[1][1], f is not None and f > 0.05, 20, "No reading" if f is None else f"CMF {f:+.3f}"),
            item("mfi", ITEMS[2][1], m is not None and 50 <= m <= 80, 15, "No reading" if m is None else f"MFI {m:.0f}"),
            item("surge", ITEMS[3][1], bool(surge), 20,
                 f"Up day on {max(surge):.2f}x volume" if surge else "No up day on 1.5x volume in the last 3 days"),
            item("ma", ITEMS[4][1], close[t] > s50[t], 20, f"Close {close[t]:.2f} vs 50-day {s50[t]:.2f}"),
        ]
        entry, stop = close[t], close[t] - 1.5 * a[t]
        out.append(finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                          entry + 2 * (entry - stop)))
    return out
