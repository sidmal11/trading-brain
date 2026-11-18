"""Small key-value record of the system's own health (last poll, AI state), in the main SQLite file."""
import json
import sqlite3
import threading


class HealthStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS health (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def set(self, key, value):
        """Store `value` (JSON); None removes the key."""
        with self._lock, self._connect() as conn:
            if value is None:
                conn.execute("DELETE FROM health WHERE key = ?", (key,))
            else:
                conn.execute("INSERT OR REPLACE INTO health VALUES (?, ?)", (key, json.dumps(value)))

    def get_all(self) -> dict:
        with self._connect() as conn:
            return {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM health").fetchall()}
