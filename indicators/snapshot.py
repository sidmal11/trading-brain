"""The latest value of every indicator for one ticker's completed daily bars (for GET /api/v1/indicators)."""
from indicators import (adr, adx, atr, atr_pct, bollinger, cmf, ema, hist_vol, keltner, macd, mfi, momentum, obv,
                        relative_volume, roc, rolling_percentile, rsi, sma, stoch_rsi)

PERCENTILE_BARS = 252  # about a year: "how unusual is today's volatility"


def _last(series):
    return series[-1] if series else None


def latest(bars) -> dict:
    """{"asOf", "bars", "values": {name: number or None}, "warmupMissing": [names still warming up]}.

    A value is None when the history is too short for it; volume indicators are None when any stored
    volume is missing (never filled in).
    """
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    values = {}
    for n in (9, 20, 50, 100, 200):
        values[f"sma{n}"] = _last(sma(close, n))
    for n in (9, 20, 50):
        values[f"ema{n}"] = _last(ema(close, n)) if len(close) >= 3 * n else None  # seed bias until ~3n bars
    line, sig, hist = macd(close) if close else ([], [], [])
    enough_for_macd = len(close) >= 3 * 26
    values.update(macd=_last(line) if enough_for_macd else None, macdSignal=_last(sig) if enough_for_macd else None,
                  macdHist=_last(hist) if enough_for_macd else None)
    values.update(rsi14=_last(rsi(close, 14)), stochRsi14=_last(stoch_rsi(close, 14)), roc12=_last(roc(close, 12)),
                  momentum10=_last(momentum(close, 10)))
    directional = adx(high, low, close, 14)
    values.update(adx14=_last(directional["adx"]), plusDi14=_last(directional["plus_di"]),
                  minusDi14=_last(directional["minus_di"]))
    pct = atr_pct(high, low, close, 14)
    values.update(atr14=_last(atr(high, low, close, 14)), atrPct14=_last(pct),
                  atrPctPercentile=_last(rolling_percentile(pct, PERCENTILE_BARS)))
    bands = bollinger(close, 20, 2.0)
    values.update(bbUpper=_last(bands["upper"]), bbMiddle=_last(bands["middle"]), bbLower=_last(bands["lower"]),
                  bbBandwidth=_last(bands["bandwidth"]), bbPercentB=_last(bands["percent_b"]),
                  bbBandwidthPercentile=_last(rolling_percentile(bands["bandwidth"], PERCENTILE_BARS)))
    channel = keltner(high, low, close, 20, 2.0, 10)
    values.update(keltnerUpper=_last(channel["upper"]) if len(close) >= 60 else None,
                  keltnerLower=_last(channel["lower"]) if len(close) >= 60 else None)
    values.update(hv20=_last(hist_vol(close, 20)) if len(close) > 1 else None, adr20=_last(adr(high, low, 20)))
    volume = [b.volume for b in bars]
    if None in volume:
        values.update(relVolume20=None, obv=None, mfi14=None, cmf20=None)
    else:
        values.update(relVolume20=_last(relative_volume(volume, 20)), obv=_last(obv(close, volume)),
                      mfi14=_last(mfi(high, low, close, volume, 14)), cmf20=_last(cmf(high, low, close, volume, 20)))
    return {"asOf": bars[-1].date if bars else None, "bars": len(bars), "values": values,
            "warmupMissing": sorted(k for k, v in values.items() if v is None)}
