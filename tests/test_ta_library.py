"""Indicator library: hand-calculated cases (worked in the comments), warm-ups, bad input, pandas equality."""
import math
import random
import statistics
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.market_source import compute_rsi  # noqa: E402
from indicators import (adr, adx, atr, atr_pct, bollinger, cmf, ema, hist_vol, keltner, macd_hist_slope, mfi,  # noqa: E402
                        momentum, obv, relative_volume, roc, rolling_percentile, rsi, slope, sma, stoch_rsi,
                        true_range)

N = None
approx = pytest.approx


def walk(n=300, seed=3):
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= 1 + rng.gauss(0.0005, 0.015)
        out.append(price)
    return out


# ── trend ─────────────────────────────────────────────────────────────────────

def test_sma_and_ema_match_pandas():
    c = walk()
    assert sma(c, 20)[19:] == approx(pd.Series(c).rolling(20).mean().tolist()[19:], rel=1e-12)
    assert sma(c, 20)[:19] == [N] * 19
    assert ema(c, 12) == approx(pd.Series(c).ewm(span=12, adjust=False).mean().tolist(), rel=1e-12)


def test_slope():
    # (4 - 1) / 2 = 1.5, (7 - 2) / 2 = 2.5
    assert slope([1, 2, 4, 7], 2) == [N, N, 1.5, 2.5]


def test_adx_hand_case():
    # +DM: 1, 2, 0   -DM: 0, 0, 1   TR (bars 1-3): 1.5, 2.5, 2.5
    # Wilder n=2: TR 2.0 then 2.25; +DM 1.5 then 0.75; -DM 0 then 0.5
    # +DI 75, 33.33; -DI 0, 22.22; DX 100, 20; ADX = (100 + 20) / 2 = 60 on bar 3 (2n - 1)
    high, low, close = [10, 11, 13, 12], [9, 9.5, 11, 10], [9.5, 10.5, 12.5, 10.5]
    out = adx(high, low, close, n=2)
    assert out["plus_di"] == approx([N, N, 75, 100 / 3])
    assert out["minus_di"] == approx([N, N, 0, 200 / 9])
    assert out["adx"] == approx([N, N, N, 60])


# ── momentum ──────────────────────────────────────────────────────────────────

def test_rsi_hand_case():
    # changes +1, -1, +2, +1; first averages over 3 changes: gain 1, loss 1/3 -> RS 3 -> 75
    # next: gain (1*2 + 1)/3 = 1, loss (1/3*2 + 0)/3 = 2/9 -> RS 4.5 -> 100 - 100/5.5
    assert rsi([10, 11, 10, 12, 13], 3) == approx([N, N, N, 75, 100 - 100 / 5.5])
    assert rsi([5, 5, 5, 5], 3)[-1] == 50.0 and rsi([1, 2, 3, 4], 3)[-1] == 100.0
    assert rsi([1, 2, 3], 3) == [N, N, N]


def test_rsi_matches_the_dashboard_futures_rsi():
    for seed in range(20):
        c = walk(60, seed)
        assert compute_rsi(c) == round(rsi(c)[-1], 2)


def test_stoch_rsi_warm_up_and_range():
    c = walk(80)
    out, r = stoch_rsi(c, 14), rsi(c, 14)
    assert out[:27] == [N] * 27 and out[27] is not None  # 2n - 1
    window = r[27 - 13:28]
    assert out[27] == approx(100 * (r[27] - min(window)) / (max(window) - min(window)))
    assert all(0 <= v <= 100 for v in out if v is not None)


def test_roc_momentum_and_histogram_slope():
    assert roc([10, 11, 12.1], 1) == approx([N, 10, 10])
    assert momentum([1, 3, 6], 2) == [N, N, 5]
    h = macd_hist_slope(walk(60))
    assert h[0] is None and len(h) == 60


# ── volatility ────────────────────────────────────────────────────────────────

def test_true_range_and_atr():
    high, low, close = [10, 12, 11], [8, 9, 7], [9, 11, 8]
    # bar 0: 10 - 8 = 2; bar 1: max(3, |12-9|, |9-9|) = 3; bar 2: max(4, |11-11|, |7-11|) = 4
    assert true_range(high, low, close) == [2, 3, 4]
    # Wilder n=2: mean(2, 3) = 2.5, then 2.5 + (4 - 2.5) / 2 = 3.25
    assert atr(high, low, close, 2) == approx([N, 2.5, 3.25])
    assert atr_pct(high, low, close, 2) == approx([N, 2.5 / 11 * 100, 3.25 / 8 * 100])


def test_bollinger_hand_case_and_pandas():
    # closes 1, 2, 3: mean 2, population sd sqrt(2/3)
    sd = math.sqrt(2 / 3)
    out = bollinger([1, 2, 3, 4], n=3, k=2)
    assert out["middle"][2] == 2 and out["upper"][2] == approx(2 + 2 * sd) and out["lower"][2] == approx(2 - 2 * sd)
    assert out["bandwidth"][2] == approx(100 * 4 * sd / 2)
    assert out["percent_b"][2] == approx((3 - (2 - 2 * sd)) / (4 * sd))
    c = walk()
    ref = pd.Series(c).rolling(20).std(ddof=0).tolist()
    got = [(u - m) / 2 for u, m in zip(bollinger(c)["upper"][19:], bollinger(c)["middle"][19:])]
    assert got == approx(ref[19:], rel=1e-9)


def test_flat_bands_give_no_percent_b():
    assert bollinger([5, 5, 5], n=3)["percent_b"][2] is None


def test_keltner_is_ema_plus_minus_atr():
    c = walk(40)
    h, lo = [x * 1.01 for x in c], [x * 0.99 for x in c]
    out, a, e = keltner(h, lo, c, n=20, mult=2, atr_n=10), atr(h, lo, c, 10), ema(c, 20)
    assert out["upper"][30] == approx(e[30] + 2 * a[30]) and out["lower"][30] == approx(e[30] - 2 * a[30])


def test_hist_vol_hand_case_and_pandas():
    # log returns ln(1.1), ln(0.9): sample sd x sqrt(252) x 100
    c = [100, 110, 99, 108.9]
    r = [math.log(1.1), math.log(0.9)]
    assert hist_vol(c, 2)[:2] == [N, N]
    assert hist_vol(c, 2)[2] == approx(statistics.stdev(r) * math.sqrt(252) * 100)
    w = walk()
    logs = pd.Series(w).apply(math.log).diff()
    ref = (logs.rolling(20).std(ddof=1) * math.sqrt(252) * 100).tolist()
    assert hist_vol(w, 20)[20:] == approx(ref[20:], rel=1e-9)


def test_adr_and_rolling_percentile():
    assert adr([11, 12], [10, 10], 2) == approx([N, 15])  # (10% + 20%) / 2
    assert rolling_percentile([3, 1, 2, 5], 3) == approx([N, N, 200 / 3, 100])
    assert rolling_percentile([N, 1, 2, 3], 2) == [N, N, 100, 100]


# ── volume ────────────────────────────────────────────────────────────────────

def test_relative_volume_obv():
    assert relative_volume([100, 100, 300], 2) == [N, N, 3.0]
    assert relative_volume([0, 0, 300], 2) == [N, N, N]
    assert obv([10, 11, 11, 9], [100, 200, 300, 400]) == [0, 200, 200, -200]


def test_mfi_hand_case():
    # typical prices 10, 11, 10, 12 with volume 100: + flows 1100, 0, 1200; - flows 0, 1000, 0
    # bar 2: 1100 / 1000 -> 100 - 100 / 2.1; bar 3: 1200 / 1000 -> 100 - 100 / 2.2
    tp = [10, 11, 10, 12]
    assert mfi(tp, tp, tp, [100] * 4, n=2) == approx([N, N, 100 - 100 / 2.1, 100 - 100 / 2.2])
    assert mfi([1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 1, 1], n=2)[-1] == 100.0


def test_cmf_hand_case():
    # multipliers +1, -1, 0 on volumes 100, 100, 200: bar 1 = 0 / 200; bar 2 = -100 / 300
    h, lo, c, v = [10, 10, 10], [8, 8, 8], [10, 8, 9], [100, 100, 200]
    assert cmf(h, lo, c, v, n=2) == approx([N, 0, -1 / 3])


# ── bad input fails loudly ────────────────────────────────────────────────────

@pytest.mark.parametrize("call", [
    lambda: sma([1, None, 3], 2),
    lambda: sma([1, 2, 3], 0),
    lambda: sma([1, 2, 3], 2.5),
    lambda: rsi([1, float("nan"), 3], 2),
    lambda: atr([1, 2], [1], [1, 2], 2),
    lambda: obv([1, 2], [True, 3]),
    lambda: hist_vol([1, 0, 2], 2),
])
def test_bad_input_raises(call):
    with pytest.raises(ValueError):
        call()


def test_futures_rsi_is_unknown_with_a_missing_close():
    assert compute_rsi([100.0 + i for i in range(20)] + [float("nan")]) is None
