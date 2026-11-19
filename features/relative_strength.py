"""Relative strength: a ticker's return minus a benchmark's over the same dates, in percentage points."""

PERIODS = (5, 20, 60, 120)

# Yahoo's sector and industry for the current universe, read 2026-11-18 (Yahoo lists UBER under Technology).
# Semiconductors compare with SMH; other sectors with their SPDR fund. Unmapped tickers get no sector comparison.
SECTOR_ETF = {
    "AAPL": "XLK", "MSFT": "XLK", "CSCO": "XLK", "ORCL": "XLK", "CRM": "XLK", "ADBE": "XLK", "UBER": "XLK",
    "NVDA": "SMH", "AMD": "SMH", "AVGO": "SMH", "INTC": "SMH",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "NKE": "XLY", "SBUX": "XLY",
    "WMT": "XLP", "COST": "XLP",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "V": "XLF", "MA": "XLF", "PYPL": "XLF",
    "XOM": "XLE", "CVX": "XLE",
    # Added with the P3 universe, read from Yahoo 2026-11-19 (Yahoo lists IREN under Financial Services).
    "CRWV": "XLK", "APLD": "XLK", "DELL": "XLK", "SMCI": "XLK", "ANET": "XLK", "NOW": "XLK", "PLTR": "XLK",
    "SNOW": "XLK", "CRWD": "XLK", "PANW": "XLK",
    "TSM": "SMH", "ARM": "SMH", "MU": "SMH", "QCOM": "SMH", "ASML": "SMH", "AMAT": "SMH", "LRCX": "SMH",
    "NBIS": "XLC", "SPOT": "XLC",
    "IREN": "XLF", "MS": "XLF",
    "VST": "XLU", "CEG": "XLU", "NEE": "XLU",
    "COP": "XLE", "OXY": "XLE", "SLB": "XLE",
    "LOW": "XLY", "MCD": "XLY",
    "TGT": "XLP", "PG": "XLP", "KO": "XLP",
    "LLY": "XLV", "UNH": "XLV", "JNJ": "XLV", "ABBV": "XLV", "PFE": "XLV",
    "CAT": "XLI", "GE": "XLI", "BA": "XLI", "LMT": "XLI", "RTX": "XLI",
}


def _price(b):
    return b.adj_close if b.adj_close is not None else b.close


def relative_returns(bars, benchmark, periods=PERIODS) -> dict:
    """{n: ticker return - benchmark return (percentage points) over the last n bars, or None}.

    Uses the ticker's own dates; benchmark bars after the ticker's last date are ignored, and a date the
    benchmark lacks gives None (never a nearby day).
    """
    if not bars or not benchmark:
        return {n: None for n in periods}
    last = bars[-1].date
    bench = {b.date: _price(b) for b in benchmark if b.date <= last}
    out = {}
    for n in periods:
        if len(bars) <= n:
            out[n] = None
            continue
        start, end = bars[-1 - n], bars[-1]
        b0, b1 = bench.get(start.date), bench.get(end.date)
        if not b0 or not b1 or not _price(start):
            out[n] = None
            continue
        out[n] = ((_price(end) / _price(start)) - (b1 / b0)) * 100
    return out
