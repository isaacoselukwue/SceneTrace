from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..db import Database, dumps
from ..ids import utc_now

MAX_SUMMARY = 240
TERMINAL = {"job.completed", "job.partial", "job.failed", "job.cancelled", "job.needs_selection"}
SUMMARIES = {
    "budget.updated": "Provider call reserved in the cost ledger.",
    "tool.started": "Investigator tool started.",
    "tool.completed": "Investigator tool finished.",
    "evidence.created": "Evidence frames extracted from the original video.",
    "claim.rejected": "A claim was blocked before publication.",
    "review.completed": "Secondary review finished.",
}


class JobEvents:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cond = threading.Condition()
        self._started: dict[str, float] = {}

    def append(self, job_id: str, event_type: str, payload: dict[str, Any] | None = None, *,
               mode: str | None = None) -> int:
        payload = dict(payload or {})
        summary = str(payload.pop("summary", SUMMARIES.get(event_type, event_type)))[:MAX_SUMMARY]
        start = self._started.setdefault(job_id, time.monotonic())
        body = {"stage": payload.pop("stage", None), "summary": summary, "elapsed_ms": int((time.monotonic() - start) * 1000),
                "mode": mode, "data": {k: v for k, v in payload.items() if isinstance(v, (str, int, bool, type(None), list))}}
        encoded = dumps(body)
        if len(encoded) > 4000:
            body["data"] = {}
            encoded = dumps(body)
        with self.db.write() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM job_events WHERE job_id = ?", (job_id,)).fetchone()
            seq = int(row["n"])
            conn.execute("INSERT INTO job_events(job_id, seq, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                         (job_id, seq, event_type, encoded, utc_now()))
        with self._cond:
            self._cond.notify_all()
        return seq

    def emitter(self, job_id: str, mode: str | None):
        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self.append(job_id, event_type, payload, mode=mode)
        return emit

    def after(self, job_id: str, seq: int) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM job_events WHERE job_id = ? AND seq > ? ORDER BY seq LIMIT 200", (job_id, seq))
        out = []
        for r in rows:
            body = json.loads(r["payload_json"])
            out.append({"job_id": job_id, "seq": r["seq"], "type": r["type"], "created_at": r["created_at"], **body})
        return out

    def wait(self, timeout: float) -> None:
        with self._cond:
            self._cond.wait(timeout)
