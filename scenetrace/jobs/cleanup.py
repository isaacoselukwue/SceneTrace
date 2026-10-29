"""Remote provider file cleanup and local retention. Local deletion is not proof of remote deletion,
so remote deletions are tracked separately and retried."""
from __future__ import annotations

import datetime as dt

from ..ids import utc_now
from ..logs import get
from ..providers.base import ProviderError
from ..providers.transport import MetaTransport
from .context import Services

log = get("cleanup")
MAX_ATTEMPTS = 5


def cleanup_remote_files(svc: Services, *, transport: object | None = None) -> int:
    """Delete provider files whose analyses are no longer active. Returns the number deleted."""
    rows = svc.db.query(
        "SELECT r.* FROM remote_files r WHERE r.deleted_at IS NULL AND r.attempts < ? AND NOT EXISTS ("
        " SELECT 1 FROM jobs j WHERE j.analysis_id = r.analysis_id AND j.state IN ('queued', 'running', 'cancel_requested'))",
        (MAX_ATTEMPTS,))
    if not rows:
        return 0
    if transport is None:
        if not svc.settings.model_api_key:
            return 0
        transport = MetaTransport(svc.settings.provider_base_url, svc.settings.model_api_key)
    deleted = 0
    for row in rows:
        try:
            transport.delete_file(row["provider_file_id"])  # type: ignore[attr-defined]
            with svc.db.write() as conn:
                conn.execute("UPDATE remote_files SET deleted_at = ?, delete_requested_at = COALESCE(delete_requested_at, ?)"
                             " WHERE id = ?", (utc_now(), utc_now(), row["id"]))
            deleted += 1
        except ProviderError as exc:
            with svc.db.write() as conn:
                conn.execute("UPDATE remote_files SET attempts = attempts + 1, last_error = ?, delete_requested_at ="
                             " COALESCE(delete_requested_at, ?) WHERE id = ?", (exc.code, utc_now(), row["id"]))
            log.warning("remote file deletion pending", extra={"event_code": "REMOTE_DELETE_PENDING"})
    return deleted


def expire_clips(svc: Services) -> int:
    """Revoke access to expired non-demo clips and remove their private media."""
    now = utc_now()
    rows = svc.db.query("SELECT * FROM clips WHERE expires_at IS NOT NULL AND expires_at < ? AND status NOT IN ('deleted', 'expired')"
                        " AND owner_id != 'public'", (now,))
    for row in rows:
        delete_clip(svc, dict(row), status="expired")
    return len(rows)


def delete_clip(svc: Services, clip: dict, *, status: str = "deleted") -> None:
    with svc.db.write() as conn:
        conn.execute("UPDATE clips SET status = ?, deleted_at = ? WHERE id = ?", (status, utc_now(), clip["id"]))
        conn.execute("UPDATE assets SET revoked_at = ? WHERE clip_id = ? AND revoked_at IS NULL", (utc_now(), clip["id"]))
        conn.execute("UPDATE jobs SET state = 'cancel_requested' WHERE clip_id = ? AND state IN ('queued', 'running')", (clip["id"],))
        conn.execute("UPDATE jobs SET state = 'cancel_requested' WHERE analysis_id IN (SELECT id FROM analyses WHERE clip_id = ?)"
                     " AND state IN ('queued', 'running')", (clip["id"],))
        conn.execute("UPDATE remote_files SET delete_requested_at = COALESCE(delete_requested_at, ?) WHERE clip_id = ?",
                     (utc_now(), clip["id"]))
    svc.storage.remove_tree(svc.storage.clip_dir(clip["owner_id"], clip["id"]))


def expiry_for(hours: int) -> str:
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
