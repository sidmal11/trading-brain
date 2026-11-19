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
    # Every volume indicator here sums only its own window, so one pass over the whole history gives the same
    # numbers as recomputing per day. A missing volume is computed as 0 and then blocks every day whose last
    # WINDOW bars include it, so no number that touched it is ever used.
    v0 = [0.0 if x is None else x for x in vol]
    o, rv = obv(close, v0), relative_volume(v0, 20)
    cmf20, mfi14 = cmf(high, low, close, v0, 20), mfi(high, low, close, v0, 14)
    missing = [0] * (n + 1)  # missing[i] = missing volumes among bars[0:i]
    for i, x in enumerate(vol):
        missing[i + 1] = missing[i] + (x is None)
    out = []
    for t in range(n):
        lo_t = t - WINDOW + 1
        gap = lo_t < 0 or missing[t + 1] - missing[lo_t] > 0
        if t < WARMUP or a[t] is None or s50[t] is None or gap:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            if t >= WARMUP and gap:
                for x in out[-1]["checklist"]:
                    x["detail"] = "No volume reading"
            continue
        f, m = cmf20[t], mfi14[t]
        traded = sum(v0[t - 19:t + 1])
        trend = (o[t] - o[t - 20]) / traded if traded else None  # same measure as the obvTrend20 feature
        surge = [rv[i] for i in (t - 2, t - 1, t) if rv[i] is not None and rv[i] >= 1.5 and close[i] > close[i - 1]]
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
