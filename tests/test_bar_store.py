"""SQLite store of completed daily bars and data-quality reports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.bar_store import BarStore  # noqa: E402
from market_data.bars import Bar  # noqa: E402


def bar(d, close=10.0, volume=1000):
    return Bar(d, close + 1, close - 1, close, close, volume, close * 0.99)


def test_daily_returns_bars_oldest_first_before_the_cutoff(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", [bar("2026-11-17"), bar("2026-11-16"), bar("2026-11-18")])
    out = store.daily(["AAPL", "MSFT"], before="2026-11-18")
    assert [b.date for b in out["AAPL"]] == ["2026-11-16", "2026-11-17"]
    assert "MSFT" not in out


def test_bar_keeps_the_old_tuple_positions(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", [Bar("2026-11-16", 12.0, 9.0, 11.0, 10.0, 500, 10.9)])
    [b] = store.daily(["AAPL"], before="2026-12-01")["AAPL"]
    assert (b[0], b[1], b[2], b[3]) == ("2026-11-16", 12.0, 9.0, 11.0)
    assert (b.open, b.volume, b.adj_close) == (10.0, 500, 10.9)


def test_upsert_overwrites_and_replace_drops_old_rows(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", [bar("2026-11-16", 10), bar("2026-11-17", 10)])
    store.upsert("AAPL", [bar("2026-11-17", 20)])
    assert [b.close for b in store.daily(["AAPL"], "2026-12-01")["AAPL"]] == [10, 20]
    store.replace("AAPL", [bar("2026-11-17", 5)])
    assert [b.date for b in store.daily(["AAPL"], "2026-12-01")["AAPL"]] == ["2026-11-17"]
    assert store.newest_date("AAPL") == "2026-11-17" and store.newest_date("MSFT") is None


def test_reports_round_trip(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.save_report("AAPL", {"status": "warn", "issues": ["Volume 0 on 2026-11-16"]})
    store.save_report("AAPL", {"status": "valid", "issues": []})
    assert store.report("AAPL")["status"] == "valid"
    assert store.report("MSFT") is None
    [r] = store.reports()
    assert r["ticker"] == "AAPL" and r["issues"] == [] and r["checkedAt"]
