"""One versioned, point-in-time feature dict per ticker per day, and its snapshot store.

`build_features(bars, spy, sector)` reads only `bars` (completed bars ending at the decision day) and the
benchmark bars up to that day. The same function serves the daily pass and, later, the backtest.
shortcut: indicators are recomputed over the whole input on every call; pass the 500-bar window, and make
them incremental if a backtest over every day gets slow.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from features.price_action import price_action
from features.relative_strength import PERIODS, relative_returns
from market_data.weekly import weekly_bars
from indicators import (adx, atr_pct, bollinger, cmf, ema, hist_vol, macd, mfi, obv, relative_volume, roc,
                        rolling_percentile, rsi, slope, sma, stoch_rsi)

FEATURE_VERSION = "2"  # bump on any change to a definition below; "2" adds timeframes (R7)


def _pct(a, b):
    return None if a is None or not b else (a / b - 1) * 100


def _last(xs):
    return xs[-1] if xs else None


def trend_state(close, short, long):
    """"up", "down" or "flat" from the close and two averages; None when either average is missing."""
    if None in (close, short, long):
        return None
    if close > long and short > long:
        return "up"
    if close < long and short < long:
        return "down"
    return "flat"


def alignment(daily, weekly):
    """strong, partial, conflicting or neutral (R7); None when either timeframe is unknown."""
    if daily is None or weekly is None:
        return None
    states = {daily, weekly}
    if states == {"up"}:
        return "strong"
    if states == {"up", "down"}:
        return "conflicting"
    return "partial" if "up" in states else "neutral"


def timeframes(bars, daily_trend) -> dict:
    """Weekly trend and momentum from completed weeks, and how the weekly trend agrees with the daily one."""
    w = weekly_bars(bars)
    wc = [b.close for b in w]
    weekly = {"trend": None, "rsi14": None, "return13Pct": None, "weeks": len(w), "asOf": w[-1].date if w else None}
    if len(wc) >= 2:
        weekly["trend"] = trend_state(wc[-1], sma(wc, 10)[-1], sma(wc, 40)[-1])
        weekly["rsi14"] = rsi(wc, 14)[-1] if len(wc) > 14 else None
        weekly["return13Pct"] = _pct(wc[-1], wc[-14]) if len(wc) > 13 else None
    return {"daily": {"trend": daily_trend}, "weekly": weekly, "alignment": alignment(daily_trend, weekly["trend"])}


def build_features(bars, spy=None, sector=None, sector_etf=None) -> dict:
    if not bars:
        return {"version": FEATURE_VERSION, "asOf": None, "coverage": 0.0}
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    n = len(close)
    s50, s200 = sma(close, 50), sma(close, 200)
    e20, e50 = ema(close, 20), ema(close, 50)
    directional = adx(high, low, close, 14)
    trend = {
        "priceVsSma50Pct": _pct(close[-1], s50[-1]),
        "priceVsSma200Pct": _pct(close[-1], s200[-1]),
        "sma50SlopePct": _pct(s50[-1], s50[-21]) if n > 70 else None,
        "ema20AboveEma50": (e20[-1] > e50[-1]) if n >= 150 else None,  # EMAs lean on their seed before ~3n bars
        "adx14": _last(directional["adx"]),
        "diSpread": None if directional["plus_di"][-1] is None else directional["plus_di"][-1] - directional["minus_di"][-1],
    }
    r = rsi(close, 14)
    hist = macd(close)[2]
    momentum = {
        "rsi14": r[-1], "rsiSlope5": _last(slope(r, 5)),
        "macdHist": hist[-1] if n >= 78 else None,
        "macdHistSlope": hist[-1] - hist[-2] if n >= 79 else None,
        "roc20": _last(roc(close, 20)), "stochRsi14": _last(stoch_rsi(close, 14)),
    }
    pct, bands = atr_pct(high, low, close, 14), bollinger(close, 20, 2.0)
    volatility = {
        "atrPct14": pct[-1], "atrPctPercentile": _last(rolling_percentile(pct, 252)),
        "bbBandwidth": bands["bandwidth"][-1], "bbBandwidthPercentile": _last(rolling_percentile(bands["bandwidth"], 252)),
        "bbPercentB": bands["percent_b"][-1], "hv20": _last(hist_vol(close, 20)) if n > 1 else None,
    }
    volume_bars = [b.volume for b in bars]
    if None in volume_bars:
        volume = {"relVolume20": None, "obvTrend20": None, "mfi14": None, "cmf20": None}
    else:
        o = obv(close, volume_bars)
        avg20 = sum(volume_bars[-20:]) / 20 if n >= 21 else None
        volume = {
            "relVolume20": _last(relative_volume(volume_bars, 20)),
            # OBV change over 20 bars as a share of the volume traded in them: -1 all selling, +1 all buying
            "obvTrend20": (o[-1] - o[-21]) / (avg20 * 20) if avg20 else None,
            "mfi14": _last(mfi(high, low, close, volume_bars, 14)),
            "cmf20": _last(cmf(high, low, close, volume_bars, 20)),
        }
    vs_spy, vs_sector = relative_returns(bars, spy or []), relative_returns(bars, sector or [])
    relative = {"vsSpy": {str(k): v for k, v in vs_spy.items()},
                "vsSector": {str(k): v for k, v in vs_sector.items()}, "sectorEtf": sector_etf}
    pa = price_action(bars)
    scalars = [*trend.values(), *momentum.values(), *volatility.values(), *volume.values(),
               *vs_spy.values(), *(vs_sector.values() if sector_etf else []),
               pa["structure"], pa["breakOfStructure"], pa["gapPct"], pa["rangeExpansion"], pa["breakout"]]
    return {
        "version": FEATURE_VERSION, "asOf": bars[-1].date, "bars": n,
        "trend": trend, "momentum": momentum, "volatility": volatility, "volume": volume,
        "relativeStrength": relative, "priceAction": pa,
        "timeframes": timeframes(bars, trend_state(close[-1], s50[-1], s200[-1])),  # not in coverage: weight 0
        "coverage": sum(v is not None for v in scalars) / len(scalars),
    }


assert PERIODS == (5, 20, 60, 120)  # the API and dashboard read these keys


class FeatureStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS feature_snapshots (ticker TEXT NOT NULL, date TEXT NOT NULL, "
                         "version TEXT NOT NULL, doc TEXT NOT NULL, created_at TEXT NOT NULL, "
                         "PRIMARY KEY (ticker, date, version))")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, ticker, features):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO feature_snapshots VALUES (?, ?, ?, ?, ?)",
                         (ticker, features["asOf"], features["version"], json.dumps(features, sort_keys=True),
                          datetime.now(timezone.utc).isoformat()))

    def latest(self, ticker) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT doc FROM feature_snapshots WHERE ticker = ? AND version = ? "
                               "ORDER BY date DESC LIMIT 1", (ticker, FEATURE_VERSION)).fetchone()
        return json.loads(row[0]) if row else None
