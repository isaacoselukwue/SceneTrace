from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .ids import utc_now

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._local = threading.local()
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn()
        with self._write_lock:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE")
            self._local.depth = depth + 1
            try:
                yield conn
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def require(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = (), what: str = "record") -> sqlite3.Row:
        row = self.one(sql, params)
        if row is None:
            from .errors import not_found
            raise not_found(what)
        return row

    def migrate(self) -> None:
        conn = self._connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name[:3])
                if version in applied:
                    continue
                script = path.read_text(encoding="utf-8").replace(
                    "CREATE TABLE schema_migrations", "CREATE TABLE IF NOT EXISTS schema_migrations")
                conn.executescript("BEGIN;" + script + ";COMMIT;")
                conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, utc_now()))
        finally:
            conn.close()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    return default if text is None else json.loads(text)
