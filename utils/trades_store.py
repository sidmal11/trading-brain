"""Mock brokerage orders in one SQLite file.

Cash and positions are never stored: both are derived from FILLED orders, so a
row is the single source of truth. Close always sells the whole position, so a
ticker's open lots are its FILLED buys newer (by id) than its latest FILLED sell.
The partial unique index allows one live (non-FAILED) buy per alert, which is what
makes a double approval place one order while a refused one can be retried.
"""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_COLUMNS = ("id", "alert_id", "ticker", "side", "quantity", "status", "fill_price", "reason",
            "broker", "strategy", "stop_price", "target_price", "trading_date", "created_at")
_WIRE = ("id", "alertId", "ticker", "side", "quantity", "status", "fillPrice", "reason",
         "broker", "strategy", "stopPrice", "targetPrice", "tradingDate", "createdAt")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM orders"


STRANDED_SECONDS = 120
STRANDED_REASON = "Interrupted: the server stopped before this order finished."


def _wire(row):
    return dict(zip(_WIRE, row)) if row else None


class TradesStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS orders ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT, ticker TEXT NOT NULL, "
                "side TEXT NOT NULL, quantity INTEGER NOT NULL, status TEXT NOT NULL, fill_price REAL, "
                "reason TEXT, broker TEXT NOT NULL, strategy TEXT, stop_price REAL, target_price REAL, "
                "trading_date TEXT, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_live_buy_per_alert ON orders(alert_id) "
                "WHERE side = 'BUY' AND status != 'FAILED'"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _fail_stranded(conn, seconds=STRANDED_SECONDS):
        """A mock fill takes milliseconds, so an order PENDING this long was cut off by a crash or restart."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        return conn.execute("UPDATE orders SET status = 'FAILED', reason = ? WHERE status = 'PENDING' "
                            "AND created_at < ?", (STRANDED_REASON, cutoff)).rowcount

    def fail_stranded(self, seconds=STRANDED_SECONDS) -> int:
        """Fail every order PENDING longer than `seconds`. The API calls this with 0 at start-up: only the one API
        process places orders, so whatever is PENDING when it starts was cut off by the stop before it."""
        with self._lock, self._connect() as conn:
            return self._fail_stranded(conn, seconds)

    def _get(self, conn, order_id):
        return _wire(conn.execute(f"{_SELECT} WHERE id = ?", (order_id,)).fetchone())

    def _insert(self, conn, alert_id, ticker, side, quantity, trading_date, strategy, stop_price, target_price):
        cur = conn.execute(
            "INSERT INTO orders (alert_id, ticker, side, quantity, status, broker, strategy, stop_price, "
            "target_price, trading_date, created_at) VALUES (?, ?, ?, ?, 'PENDING', 'mock', ?, ?, ?, ?, ?)",
            (alert_id, ticker, side, quantity, strategy, stop_price, target_price, trading_date,
             datetime.now(timezone.utc).isoformat()),
        )
        return self._get(conn, cur.lastrowid)

    def claim_buy(self, alert_id, ticker, quantity, trading_date, strategy=None, stop_price=None, target_price=None):
        """Insert a PENDING buy. Returns (order, True), or (existing live buy, False) if one exists."""
        with self._lock:
            with self._connect() as conn:
                self._fail_stranded(conn)
            for attempt in range(2):
                try:
                    with self._connect() as conn:
                        return self._insert(conn, alert_id, ticker, "BUY", quantity, trading_date,
                                            strategy, stop_price, target_price), True
                except sqlite3.IntegrityError as e:
                    # The partial unique index is the only constraint on alert_id.
                    if "orders.alert_id" not in str(e) or attempt == 1:
                        raise
                with self._connect() as conn:
                    existing = self._live_buy(conn, alert_id)
                if existing:
                    return existing, False
                # The conflicting buy was marked FAILED in between: retry the insert once.

    def live_buy(self, alert_id):
        """The alert's buy that is pending or filled, or None. Read only: the dashboard calls it on every poll."""
        with self._connect() as conn:
            return self._live_buy(conn, alert_id)

    def _live_buy(self, conn, alert_id):
        return _wire(conn.execute(
            f"{_SELECT} WHERE alert_id = ? AND side = 'BUY' AND status != 'FAILED'", (alert_id,)
        ).fetchone())

    def claim_sell(self, ticker, trading_date):
        """Insert a PENDING sell of the whole open position, or return None when there is
        nothing to sell or an order (buy or sell) for this ticker is already in flight."""
        with self._lock, self._connect() as conn:
            # The lock is per store object; BEGIN IMMEDIATE serialises across objects and processes.
            conn.execute("BEGIN IMMEDIATE")
            self._fail_stranded(conn)
            # A pending buy too: selling only the filled lots would orphan its shares once it fills.
            if conn.execute("SELECT 1 FROM orders WHERE ticker = ? AND status = 'PENDING'",
                            (ticker,)).fetchone():
                return None
            quantity = sum(o["quantity"] for o in self._open_lots(conn).get(ticker, []))
            if quantity <= 0:
                return None
            return self._insert(conn, None, ticker, "SELL", quantity, trading_date, None, None, None)

    def fill(self, order_id, fill_price):
        """Mark a PENDING order FILLED. A non-PENDING order is returned unchanged."""
        if isinstance(fill_price, bool) or not isinstance(fill_price, (int, float)) or not fill_price > 0:
            raise ValueError(f"fill_price must be a positive number, got {fill_price!r}")
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE orders SET status = 'FILLED', fill_price = ? WHERE id = ? AND status = 'PENDING'",
                         (fill_price, order_id))
            return self._get(conn, order_id)

    def fail(self, order_id, reason):
        """Mark a PENDING order FAILED. A non-PENDING order is returned unchanged."""
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE orders SET status = 'FAILED', reason = ? WHERE id = ? AND status = 'PENDING'",
                         (reason, order_id))
            return self._get(conn, order_id)

    def list_orders(self, limit=200):
        with self._connect() as conn:
            return [_wire(r) for r in conn.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,))]

    def filled_buys_on(self, trading_date):
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM orders WHERE side = 'BUY' AND status = 'FILLED' AND trading_date = ?",
                (trading_date,),
            ).fetchone()[0]

    def _filled(self):
        with self._connect() as conn:
            return self._filled_on(conn)

    @staticmethod
    def _filled_on(conn):
        return [_wire(r) for r in conn.execute(f"{_SELECT} WHERE status = 'FILLED' ORDER BY id")]

    def _open_lots(self, conn):
        lots = {}
        for o in self._filled_on(conn):
            if o["side"] == "SELL":
                lots[o["ticker"]] = []  # Close sells the whole position
            else:
                lots.setdefault(o["ticker"], []).append(o)
        return lots

    def open_positions(self):
        with self._connect() as conn:
            lots = self._open_lots(conn)
        positions = []
        for ticker, open_lots in lots.items():
            quantity = sum(o["quantity"] for o in open_lots)
            if quantity <= 0:
                continue
            cost = sum(o["quantity"] * o["fillPrice"] for o in open_lots)
            newest = open_lots[-1]
            # costBasis is unrounded so P/L is exact; only averageCost is rounded for display.
            positions.append({"ticker": ticker, "quantity": quantity, "averageCost": round(cost / quantity, 2),
                              "costBasis": cost,
                              "stopPrice": newest["stopPrice"], "targetPrice": newest["targetPrice"]})
        return positions

    def cash(self, starting_cash):
        flow = sum(o["quantity"] * o["fillPrice"] * (-1 if o["side"] == "BUY" else 1) for o in self._filled())
        return round(starting_cash + flow, 2)
