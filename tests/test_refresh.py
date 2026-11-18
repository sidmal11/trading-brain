"""Daily refresh of the bar store: backfill, incremental, re-download after a split, outages."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.bar_store import BarStore  # noqa: E402
from market_data.bars import Bar  # noqa: E402
from market_data.refresh import refresh_daily  # noqa: E402

TODAY = date(2026, 11, 18)
DATES = ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"]


def bars(dates=DATES, scale=1.0):
    return [Bar(d, 11.0 * scale, 9.0 * scale, 10.0 * scale, 10.0 * scale, 1000, 10.0 * scale) for d in dates]


class Download:
    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, tickers, start=None, period=None):
        self.calls.append((sorted(tickers), start, period))
        return {t: v for t, v in self.data.items() if t in tickers}


def test_first_run_backfills_five_years_and_never_stores_today(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    dl = Download({"AAPL": bars(), "SPY": bars()})
    out = refresh_daily(store, TODAY, ["AAPL", "SPY"], download=dl)
    assert dl.calls == [(["AAPL", "SPY"], None, "5y")]
    assert store.newest_date("AAPL") == "2026-11-17"
    assert out["failed"] is False and store.report("AAPL")["status"] == "valid"


def test_later_run_downloads_from_ten_days_before_the_newest_bar(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(DATES[:3]))
    dl = Download({"AAPL": bars()})
    refresh_daily(store, TODAY, ["AAPL"], download=dl)
    assert dl.calls == [(["AAPL"], "2026-11-06", None)]
    assert store.newest_date("AAPL") == "2026-11-17"


def test_a_re_adjusted_history_is_downloaded_again_in_full(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(["2026-11-10", "2026-11-11"] + DATES[:3]))
    dl = Download({"AAPL": bars(scale=0.5)})  # a 2-for-1 split halves every past price
    out = refresh_daily(store, TODAY, ["AAPL"], download=dl)
    assert out["refetched"] == ["AAPL"]
    assert dl.calls[-1] == (["AAPL"], None, "5y")
    stored = store.daily(["AAPL"], "2026-12-01")["AAPL"]
    assert [b.date for b in stored] == DATES[:4]  # the old rows are replaced, not merged
    assert {b.close for b in stored} == {5.0}


def test_an_outage_changes_nothing_and_reports_failure(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(DATES[:3]))
    store.save_report("AAPL", {"status": "valid", "issues": []})
    out = refresh_daily(store, TODAY, ["AAPL"], download=Download({}))
    assert out["failed"] is True
    assert store.newest_date("AAPL") == "2026-11-16" and store.report("AAPL")["status"] == "valid"


def test_a_ticker_missing_from_the_batch_keeps_its_rows_and_goes_stale(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(DATES[:3]))
    store.upsert("MSFT", bars(DATES[:3]))
    refresh_daily(store, TODAY, ["AAPL", "MSFT"], download=Download({"AAPL": bars()}))
    assert store.newest_date("MSFT") == "2026-11-16"
    report = store.report("MSFT")
    assert report["status"] == "invalid" and "Stale" in report["issues"][0]


def test_gaps_are_checked_against_spy(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    holey = [d for d in DATES if d != "2026-11-13"]
    refresh_daily(store, TODAY, ["AAPL", "SPY"], download=Download({"AAPL": bars(holey), "SPY": bars()}))
    assert store.report("AAPL")["status"] == "warn"
    assert store.report("SPY")["status"] == "valid"


def test_tickers_missing_from_a_batch_are_reported_so_the_poll_retries(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(DATES[:3]))
    store.upsert("MSFT", bars(DATES[:3]))
    out = refresh_daily(store, TODAY, ["AAPL", "MSFT"], download=Download({"AAPL": bars()}))
    assert out["failed"] is False and out["missing"] == ["MSFT"]
    assert store.newest_date("AAPL") == "2026-11-17"  # what arrived is kept


def test_an_empty_re_download_after_a_split_counts_as_missing(tmp_path):
    store = BarStore(str(tmp_path / "s.db"))
    store.upsert("AAPL", bars(["2026-11-10", "2026-11-11"] + DATES[:3]))

    def download(tickers, start=None, period=None):
        return {} if period else {"AAPL": bars(scale=0.5)}

    out = refresh_daily(store, TODAY, ["AAPL"], download=download)
    assert out["missing"] == ["AAPL"] and out["refetched"] == []
    assert {b.close for b in store.daily(["AAPL"], "2026-12-01")["AAPL"]} == {10.0}  # old rows untouched
