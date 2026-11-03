from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .config import Settings
from .db import Database
from .errors import AppError
from .ids import new_id, utc_now

SESSION_COOKIE = "st_session"
SESSION_HOURS = 12
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Session:
    id: str
    owner_id: str
    scope: str
    csrf_token: str
    expires_at: str

    @property
    def is_owner(self) -> bool:
        return self.scope == "owner"


def owner_for_code(code: str) -> str:
    return "owner_" + hashlib.sha256(b"scenetrace-owner:" + code.encode()).hexdigest()[:12]


class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise AppError("RATE_LIMITED", "Too many requests. Wait a moment and try again.", status_code=429, retryable=True)
            hits.append(now)


class Sessions:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.attempts = RateLimiter(10, 60)

    def current_owner_id(self) -> str | None:
        configured = self.settings.demo_access_code
        if configured:
            return owner_for_code(configured)
        return "owner_local" if self.settings.bind_host in LOCAL_HOSTS else None

    def create(self, access_code: str | None, client: str, current: Session | None = None) -> Session:
        configured = self.settings.demo_access_code
        local_owner = not configured and self.settings.bind_host in LOCAL_HOSTS
        if access_code is None and current is not None and (current.is_owner or not local_owner):
            return current
        if configured:
            if access_code is None:
                return self._insert("public", "public")
            self.attempts.check(f"session:{client}")
            if not hmac.compare_digest(access_code.encode(), configured.encode()):
                raise AppError("ACCESS_DENIED", "The access code was not accepted.", status_code=401)
            return self._insert(owner_for_code(configured), "owner")
        if self.settings.bind_host in LOCAL_HOSTS:
            return self._insert("owner_local", "owner")
        return self._insert("public", "public")

    def _insert(self, owner_id: str, scope: str) -> Session:
        session = Session(id=new_id("sess"), owner_id=owner_id, scope=scope, csrf_token=secrets.token_urlsafe(32),
                          expires_at=(dt.datetime.now(dt.UTC) + dt.timedelta(hours=SESSION_HOURS))
                          .isoformat(timespec="seconds").replace("+00:00", "Z"))
        with self.db.write() as conn:
            conn.execute("INSERT INTO sessions(id, owner_id, csrf_token, scope, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (session.id, owner_id, session.csrf_token, scope, utc_now(), session.expires_at))
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id or not session_id.startswith("sess_"):
            return None
        row = self.db.one("SELECT * FROM sessions WHERE id = ? AND revoked_at IS NULL", (session_id,))
        if row is None or row["expires_at"] < utc_now()[:19] + "Z":
            return None
        if row["scope"] == "owner" and row["owner_id"] != self.current_owner_id():
            return None
        return Session(id=row["id"], owner_id=row["owner_id"], scope=row["scope"], csrf_token=row["csrf_token"],
                       expires_at=row["expires_at"])

    def revoke(self, session_id: str) -> None:
        with self.db.write() as conn:
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (utc_now(), session_id))


def check_csrf(session: Session | None, header_token: str | None) -> None:
    if session is None or not header_token or not hmac.compare_digest(session.csrf_token, header_token):
        raise AppError("CSRF_REJECTED", "The request could not be verified. Reload the page and try again.", status_code=403)
