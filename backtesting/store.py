"""Stored backtest runs, their trades, and live signal outcomes, in the main SQLite file."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

_TRADE_COLS = ("ticker", "signal_date", "entry_date", "entry", "exit_date", "exit", "reason", "ret", "mfe", "mae",
               "bars_held")
_OUTCOME_COLS = ("alertId", "ticker", "strategy", "signalDate", "entry", "stop", "target", "reason", "exitDate",
                 "exit", "ret", "mfe", "mae", "barsHeld")


class ResearchStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS backtest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "strategy TEXT NOT NULL, strategy_version TEXT NOT NULL, backtest_version TEXT NOT NULL, "
                         "params TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute(f"CREATE TABLE IF NOT EXISTS backtest_trades (run_id INTEGER NOT NULL, "
                         f"{', '.join(_TRADE_COLS)})")
            conn.execute("CREATE INDEX IF NOT EXISTS backtest_trades_run ON backtest_trades (run_id)")
            conn.execute("CREATE TABLE IF NOT EXISTS signal_outcomes (alert_id TEXT PRIMARY KEY, strategy TEXT, "
                         "doc TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS strategy_results (ticker TEXT NOT NULL, date TEXT NOT NULL, "
                         "strategy TEXT NOT NULL, version TEXT NOT NULL, doc TEXT NOT NULL, "
                         "PRIMARY KEY (ticker, date, strategy, version))")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save_run(self, strategy, strategy_version, backtest_version, params, result, trades) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            run_id = conn.execute(
                "INSERT INTO backtest_runs (strategy, strategy_version, backtest_version, params, result, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (strategy, strategy_version, backtest_version, json.dumps(params), json.dumps(result), now)).lastrowid
            conn.executemany(f"INSERT INTO backtest_trades VALUES (?, {', '.join('?' * len(_TRADE_COLS))})",
                             [(run_id, *(t.get(c) for c in _TRADE_COLS)) for t in trades])
        return run_id

    def latest_run(self, strategy) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT id, strategy_version, backtest_version, params, result, created_at "
                               "FROM backtest_runs WHERE strategy = ? ORDER BY id DESC LIMIT 1", (strategy,)).fetchone()
        if not row:
            return None
        run_id, sv, bv, params, result, created = row
        return {"id": run_id, "strategy": strategy, "strategyVersion": sv, "backtestVersion": bv,
                "params": json.loads(params), "result": json.loads(result), "createdAt": created}

    def run_trades(self, run_id, limit=500) -> list:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {', '.join(_TRADE_COLS)} FROM backtest_trades WHERE run_id = ? "
                                "ORDER BY signal_date DESC LIMIT ?", (run_id, limit)).fetchall()
        return [dict(zip(_TRADE_COLS, r)) for r in rows]

    def save_results(self, rows):
        """Each strategy's result per ticker for its newest bar (the daily pass)."""
        with self._lock, self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO strategy_results VALUES (?, ?, ?, ?, ?)",
                             [(r["ticker"], r["asOf"], r["strategy"], r["version"], json.dumps(r)) for r in rows])

    def latest_results(self, strategy) -> list:
        """The strategy's results for the newest stored date, one per ticker."""
        with self._connect() as conn:
            day = conn.execute("SELECT MAX(date) FROM strategy_results WHERE strategy = ?", (strategy,)).fetchone()[0]
            rows = conn.execute("SELECT doc FROM strategy_results WHERE strategy = ? AND date = ? ORDER BY ticker",
                                (strategy, day)).fetchall() if day else []
        return [json.loads(r[0]) for r in rows]

    def save_outcomes(self, rows):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO signal_outcomes VALUES (?, ?, ?, ?)",
                             [(r["alertId"], r["strategy"], json.dumps(r), now) for r in rows])

    def outcomes(self, strategy=None) -> list:
        with self._connect() as conn:
            if strategy:
                rows = conn.execute("SELECT doc FROM signal_outcomes WHERE strategy = ?", (strategy,)).fetchall()
            else:
                rows = conn.execute("SELECT doc FROM signal_outcomes").fetchall()
        return sorted((json.loads(r[0]) for r in rows), key=lambda r: r["signalDate"], reverse=True)
