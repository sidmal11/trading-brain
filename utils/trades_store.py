"""Mock brokerage orders in one SQLite file.

Cash and positions are never stored: both are derived from FILLED orders, so a
row is the single source of truth. Close always sells the whole position, so a
ticker's open lots are its FILLED buys newer (by id) than its latest FILLED sell.
The partial unique index allows one live (non-FAILED) buy per account and alert, which is what
makes a double approval place one order while a refused one can be retried.
Paper trading adds manual buys (strategy 'manual', no alert) and a reset that archives
the account's orders into orders_archive and sets a new starting balance.
Every order belongs to an account (one per signed-in email); the shared account of earlier versions is
moved into the owner's account once by migrate_shared.
"""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_COLUMNS = ("id", "alert_id", "ticker", "side", "quantity", "status", "fill_price", "reason",
            "broker", "strategy", "stop_price", "target_price", "trading_date", "created_at", "account_id")
_WIRE = ("id", "alertId", "ticker", "side", "quantity", "status", "fillPrice", "reason",
         "broker", "strategy", "stopPrice", "targetPrice", "tradingDate", "createdAt", "accountId")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM orders"
_ACCOUNT_SELECT = "SELECT id, email, kind, enabled, approvals_buy, starting_cash FROM accounts"


STRANDED_SECONDS = 120
MANUAL = "manual"  # strategy of a buy placed by hand on the Paper Trading page
STRANDED_REASON = "Interrupted: the server stopped before this order finished."


def _wire(row):
    return dict(zip(_WIRE, row)) if row else None


def _account(row):
    if not row:
        return None
    return {"id": row[0], "email": row[1], "kind": row[2], "enabled": bool(row[3]),
            "approvalsBuy": bool(row[4]), "startingCash": row[5]}


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
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS accounts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, kind TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1, approvals_buy INTEGER NOT NULL DEFAULT 0, "
                "starting_cash REAL NOT NULL, UNIQUE(email, kind))"
            )
            for table in ("orders", "orders_archive"):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                if cols and "account_id" not in cols:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER")
                    except sqlite3.OperationalError:  # another process added it between the check and now
                        pass
            conn.execute("DROP INDEX IF EXISTS one_live_buy_per_alert")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_live_buy_per_account_alert ON orders(account_id, alert_id) "
                "WHERE side = 'BUY' AND status != 'FAILED'"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    # ── accounts ──────────────────────────────────────────────────────────────

    def ensure_account(self, email, default_cash, kind="mock"):
        """The account for (email, kind), created with trading on, Telegram approvals off and `default_cash`."""
        email = email.strip().lower()
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO accounts (email, kind, starting_cash) VALUES (?, ?, ?)",
                         (email, kind, float(default_cash)))
            return _account(conn.execute(f"{_ACCOUNT_SELECT} WHERE email = ? AND kind = ?", (email, kind)).fetchone())

    def get_account(self, email, kind="mock"):
        with self._connect() as conn:
            return _account(conn.execute(f"{_ACCOUNT_SELECT} WHERE email = ? AND kind = ?",
                                         (email.strip().lower(), kind)).fetchone())

    def list_accounts(self):
        with self._connect() as conn:
            return [_account(r) for r in conn.execute(f"{_ACCOUNT_SELECT} ORDER BY id")]

    def update_account(self, account_id, enabled=None, approvals_buy=None):
        with self._lock, self._connect() as conn:
            if enabled is not None:
                conn.execute("UPDATE accounts SET enabled = ? WHERE id = ?", (int(enabled), account_id))
            if approvals_buy is not None:
                conn.execute("UPDATE accounts SET approvals_buy = ? WHERE id = ?", (int(approvals_buy), account_id))
            return _account(conn.execute(f"{_ACCOUNT_SELECT} WHERE id = ?", (account_id,)).fetchone())

    def migrate_shared(self, owner_email, default_cash):
        """Once, at start-up: give the earlier shared account's orders (and its reset balance) to the owner's
        account. Rows already assigned are left alone, so a second call assigns nothing."""
        email = owner_email.strip().lower()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO accounts (email, kind, starting_cash) VALUES (?, 'mock', ?)",
                         (email, float(default_cash)))
            owner = conn.execute("SELECT id FROM accounts WHERE email = ? AND kind = 'mock'", (email,)).fetchone()[0]
            assigned = conn.execute("UPDATE orders SET account_id = ? WHERE account_id IS NULL", (owner,)).rowcount
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders_archive'").fetchone():
                assigned += conn.execute("UPDATE orders_archive SET account_id = ? WHERE account_id IS NULL",
                                         (owner,)).rowcount
            row = conn.execute("SELECT value FROM meta WHERE key = 'starting_cash'").fetchone()
            if row:
                conn.execute("UPDATE accounts SET starting_cash = ? WHERE id = ?", (float(row[0]), owner))
                conn.execute("DELETE FROM meta WHERE key = 'starting_cash'")
            return assigned

    # ── orders ────────────────────────────────────────────────────────────────

    @staticmethod
    def _fail_stranded(conn, seconds=STRANDED_SECONDS):
        """A mock fill takes milliseconds, so an order PENDING this long was cut off by a crash or restart."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        return conn.execute("UPDATE orders SET status = 'FAILED', reason = ? WHERE status = 'PENDING' "
                            "AND created_at < ?", (STRANDED_REASON, cutoff)).rowcount

    def fail_stranded(self, seconds=STRANDED_SECONDS) -> int:
        """Fail every order PENDING longer than `seconds`, for every account. The API calls this with 0 at
        start-up: only the one API process places orders, so whatever is PENDING when it starts was cut off by
        the stop before it."""
        with self._lock, self._connect() as conn:
            return self._fail_stranded(conn, seconds)

    def _get(self, conn, order_id):
        return _wire(conn.execute(f"{_SELECT} WHERE id = ?", (order_id,)).fetchone())

    def _insert(self, conn, account_id, alert_id, ticker, side, quantity, trading_date, strategy, stop_price,
                target_price):
        cur = conn.execute(
            "INSERT INTO orders (account_id, alert_id, ticker, side, quantity, status, broker, strategy, "
            "stop_price, target_price, trading_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'PENDING', 'mock', ?, ?, ?, ?, ?)",
            (account_id, alert_id, ticker, side, quantity, strategy, stop_price, target_price, trading_date,
             datetime.now(timezone.utc).isoformat()),
        )
        return self._get(conn, cur.lastrowid)

    def claim_buy(self, account_id, alert_id, ticker, quantity, trading_date, strategy=None, stop_price=None,
                  target_price=None):
        """Insert a PENDING buy. Returns (order, True), or (existing live buy, False) if one exists."""
        with self._lock:
            with self._connect() as conn:
                self._fail_stranded(conn)
            for attempt in range(2):
                try:
                    with self._connect() as conn:
                        return self._insert(conn, account_id, alert_id, ticker, "BUY", quantity, trading_date,
                                            strategy, stop_price, target_price), True
                except sqlite3.IntegrityError as e:
                    # The partial unique index is the only constraint on alert_id.
                    if "orders.alert_id" not in str(e) or attempt == 1:
                        raise
                with self._connect() as conn:
                    existing = self._live_buy(conn, account_id, alert_id)
                if existing:
                    return existing, False
                # The conflicting buy was marked FAILED in between: retry the insert once.

    def claim_manual_buy(self, account_id, ticker, quantity, trading_date):
        """Insert a PENDING hand-placed buy (no alert, so the one-buy-per-alert index never applies)."""
        with self._lock, self._connect() as conn:
            self._fail_stranded(conn)
            return self._insert(conn, account_id, None, ticker, "BUY", quantity, trading_date, MANUAL, None, None)

    def live_buy(self, account_id, alert_id):
        """The account's buy for the alert that is pending or filled, or None. Read only: the dashboard calls it
        on every poll."""
        with self._connect() as conn:
            return self._live_buy(conn, account_id, alert_id)

    def _live_buy(self, conn, account_id, alert_id):
        return _wire(conn.execute(
            f"{_SELECT} WHERE account_id = ? AND alert_id = ? AND side = 'BUY' AND status != 'FAILED'",
            (account_id, alert_id)
        ).fetchone())

    def claim_sell(self, account_id, ticker, trading_date):
        """Insert a PENDING sell of the whole open position, or return None when there is
        nothing to sell or an order (buy or sell) for this ticker is already in flight."""
        with self._lock, self._connect() as conn:
            # The lock is per store object; BEGIN IMMEDIATE serialises across objects and processes.
            conn.execute("BEGIN IMMEDIATE")
            self._fail_stranded(conn)
            # A pending buy too: selling only the filled lots would orphan its shares once it fills.
            if conn.execute("SELECT 1 FROM orders WHERE account_id = ? AND ticker = ? AND status = 'PENDING'",
                            (account_id, ticker)).fetchone():
                return None
            quantity = sum(o["quantity"] for o in self._open_lots(conn, account_id).get(ticker, []))
            if quantity <= 0:
                return None
            return self._insert(conn, account_id, None, ticker, "SELL", quantity, trading_date, None, None, None)

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

    def list_orders(self, account_id, limit=200):
        with self._connect() as conn:
            return [_wire(r) for r in conn.execute(
                f"{_SELECT} WHERE account_id = ? ORDER BY id DESC LIMIT ?", (account_id, limit))]

    def filled_buys_on(self, account_id, trading_date):
        with self._connect() as conn:
            return conn.execute(
                # The daily cap is on the system's buys; hand-placed paper trades do not use it up.
                "SELECT COUNT(*) FROM orders WHERE account_id = ? AND side = 'BUY' AND status = 'FILLED' "
                "AND trading_date = ? AND (strategy IS NULL OR strategy != ?)",
                (account_id, trading_date, MANUAL),
            ).fetchone()[0]

    def _filled(self, account_id):
        with self._connect() as conn:
            return self._filled_on(conn, account_id)

    @staticmethod
    def _filled_on(conn, account_id):
        return [_wire(r) for r in conn.execute(
            f"{_SELECT} WHERE account_id = ? AND status = 'FILLED' ORDER BY id", (account_id,))]

    def _open_lots(self, conn, account_id):
        lots = {}
        for o in self._filled_on(conn, account_id):
            if o["side"] == "SELL":
                lots[o["ticker"]] = []  # Close sells the whole position
            else:
                lots.setdefault(o["ticker"], []).append(o)
        return lots

    def open_positions(self, account_id):
        with self._connect() as conn:
            lots = self._open_lots(conn, account_id)
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

    def reset(self, account_id, starting_cash):
        """Start this account over: archive its orders and set a new starting balance. Returns False, changing
        nothing, while one of its orders is in flight (it would fill into the new account)."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fail_stranded(conn)
            if conn.execute("SELECT 1 FROM orders WHERE account_id = ? AND status = 'PENDING'",
                            (account_id,)).fetchone():
                return False
            cols = ", ".join(_COLUMNS)
            conn.execute(f"CREATE TABLE IF NOT EXISTS orders_archive AS SELECT {cols}, '' AS reset_at FROM orders WHERE 0")
            conn.execute(f"INSERT INTO orders_archive ({cols}, reset_at) SELECT {cols}, ? FROM orders "
                         "WHERE account_id = ?", (datetime.now(timezone.utc).isoformat(), account_id))
            conn.execute("DELETE FROM orders WHERE account_id = ?", (account_id,))
            conn.execute("UPDATE accounts SET starting_cash = ? WHERE id = ?", (float(starting_cash), account_id))
            return True

    def cash(self, account_id):
        """Starting balance plus the account's filled sells minus its filled buys."""
        with self._connect() as conn:
            start = conn.execute("SELECT starting_cash FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]
        flow = sum(o["quantity"] * o["fillPrice"] * (-1 if o["side"] == "BUY" else 1) for o in self._filled(account_id))
        return round(start + flow, 2)
