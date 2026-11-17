import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies import history, macd, volume  # noqa: E402
from strategies.indicators import macd_buy  # noqa: E402
from tests.test_indicators import bars_from, rising  # noqa: E402


def frame(tickers, last="2026-10-06", n=5, nan_last=None):
    idx = pd.date_range(end=last, periods=n)
    cols = {}
    for t in tickers:
        for f in ("High", "Low", "Close"):
            cols[(t, f)] = [10.0] * n
    df = pd.DataFrame(cols, index=idx)
    if nan_last:
        df.loc[idx[-1], (nan_last, "Close")] = float("nan")
    return df


def setup_function():
    history._cache.clear()


def test_todays_bar_and_nan_rows_dropped():
    with patch("strategies.history.yf.download", return_value=frame(["A", "B"], nan_last="B")):
        out = history.completed_bars(["A", "B"], "2026-10-06")
    assert out["A"][-1][0] == "2026-10-05" and len(out["A"]) == 4
    assert out["B"][-1][0] == "2026-10-05"  # NaN row dated today is gone either way
    with patch("strategies.history.yf.download", return_value=frame(["B"], last="2026-10-05", nan_last="B")):
        history._cache.clear()
        out = history.completed_bars(["B"], "2026-10-06")
    assert len(out["B"]) == 4 and out["B"][-1][0] == "2026-10-04"


def test_one_download_per_day():
    with patch("strategies.history.yf.download", return_value=frame(["A"])) as dl:
        history.completed_bars(["A"], "2026-10-06")
        history.completed_bars(["A"], "2026-10-06")
        assert dl.call_count == 1
        history.completed_bars(["A"], "2026-10-07")
        assert dl.call_count == 2


def test_failure_not_cached():
    with patch("strategies.history.yf.download", side_effect=RuntimeError("down")) as dl:
        assert history.completed_bars(["A"], "2026-10-06") == {}
    with patch("strategies.history.yf.download", return_value=frame(["A"])) as dl:
        assert "A" in history.completed_bars(["A"], "2026-10-06")
        assert dl.call_count == 1


def test_volume_evaluate():
    bars = bars_from(rising())
    assert volume.evaluate("A", bars, {"volume_score": 2.9, "price": 100.0}, 3.0) is None
    r = volume.evaluate("A", bars, {"volume_score": 3.4, "price": 100.0}, 3.0)
    assert r["strategy"] == "volume" and r["signalType"] == "VOLUME_BREAKTHROUGH"
    v = next(x for x in r["checklist"] if x["key"] == "volume")
    assert v["passed"] and v["detail"] == "Volume 3.40x the average (threshold 3.0x)"
    assert r["entryPrice"] == 100.0


def test_macd_evaluate():
    assert macd.evaluate("A", bars_from(rising()), None, 3.0) is None
    # found by search: a 7-bar dip with a 4-point pullback, then a rebound
    c = [100 + 0.3 * i for i in range(300)]
    for k in range(7):
        c[293 + k] = c[292] - 4 if k < 3 else c[292] - 4 + (k - 2) * 1.5
    bars = bars_from(c)
    r = macd.evaluate("A", bars, 3.5, 3.0)
    assert r["strategy"] == "macd" and r["signalType"] == "MACD_CROSSOVER"
    assert macd_buy(r) and r["entryPrice"] == round(c[-1], 2)
    v = next(x for x in r["checklist"] if x["key"] == "volume")
    assert v["passed"] and "3.50x" in v["detail"]
    v = next(x for x in macd.evaluate("A", bars, None, 3.0)["checklist"] if x["key"] == "volume")
    assert not v["passed"] and v["detail"] == "No volume reading"


def test_strategy_notes():
    from strategies import strategy_notes
    n = strategy_notes("macd")
    assert 0 < len(n) <= 6000
    assert n.startswith("# MACD")  # the chat preamble before the first heading is dropped
    assert strategy_notes("volume") == ""
