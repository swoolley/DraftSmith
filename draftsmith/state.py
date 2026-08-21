from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class StateStore:
    """Stores identifiers only; message content never enters the local database."""

    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS processed (message_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, processed_at INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def is_processed(self, message_id: str) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("SELECT 1 FROM processed WHERE message_id=?", (message_id,)).fetchone() is not None

    def record(self, message_id: str, draft_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR IGNORE INTO processed VALUES (?, ?, ?)", (message_id, draft_id, int(time.time())))

    def get(self, key: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
