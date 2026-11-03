from __future__ import annotations

import hashlib
from typing import Any

from reference_core.integrity import canonical_bytes

from .db import Database, dumps, loads
from .domain import (
    AssessmentRecord,
    Candidate,
    Coverage,
    Entity,
    EventRecord,
    EventRevision,
    EvidenceItem,
    FrameMap,
    Observation,
    TrackingResult,
    Zone,
)
from .errors import not_found
from .ids import utc_now
from .storage import Storage


def record_digest(record: EventRecord) -> str:
    return hashlib.sha256(canonical_bytes(record.model_dump(mode="json"))).hexdigest()


class AnalysisStore:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db = db
        self.storage = storage

    def analysis(self, analysis_id: str, owner_id: str | None = None) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        if row is None or (owner_id is not None and row["owner_id"] != owner_id):
            raise not_found("analysis")
        return dict(row)

    def clip(self, clip_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM clips WHERE id = ?", (clip_id,))
        if row is None:
            raise not_found("clip")
        return dict(row)

    def frame_map(self, clip_id: str) -> FrameMap:
        clip = self.clip(clip_id)
        path = self.storage.proxy_dir(clip["owner_id"], clip_id) / "frame_map.json"
        return FrameMap.model_validate_json(path.read_text(encoding="utf-8"))

    def zones(self, analysis_id: str) -> list[Zone]:
        rows = self.db.query("SELECT entity_id, label, polygon_json FROM entities WHERE analysis_id = ? AND kind = 'zone'"
                             " ORDER BY entity_id", (analysis_id,))
        return [Zone(zone_id=r["entity_id"], label=r["label"], polygon=[tuple(p) for p in loads(r["polygon_json"], [])])
                for r in rows]

    def save_tracking(self, analysis_id: str, owner_id: str, result: TrackingResult, cache_key: str,
                      raw_asset_id: str | None) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tracking_requests(request_key, analysis_id, owner_id, role, concept, prompt_kind, model_id,"
                " cache_key, provider_response_id, provider_call_id, outcome, raw_asset_id, raw_sha256, result_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.request_key, analysis_id, owner_id, result.role, result.concept, result.prompt_kind, result.model_id,
                 cache_key, result.provider_response_id, ",".join(result.provider_call_ids), result.outcome, raw_asset_id,
                 result.raw_digest, result.model_dump_json(), utc_now()))

    def tracking_results(self, analysis_id: str) -> list[TrackingResult]:
        rows = self.db.query("SELECT result_json FROM tracking_requests WHERE analysis_id = ? ORDER BY created_at", (analysis_id,))
        return [TrackingResult.model_validate_json(r["result_json"]) for r in rows]

    def cached_tracking(self, owner_id: str, cache_key: str) -> TrackingResult | None:
        row = self.db.one("SELECT result_json FROM tracking_requests WHERE owner_id = ? AND cache_key = ? AND outcome = 'completed'"
                          " ORDER BY created_at DESC LIMIT 1", (owner_id, cache_key))
        return TrackingResult.model_validate_json(row["result_json"]) if row else None

    def save_observations(self, analysis_id: str, observations: list[Observation], entities: list[Entity],
                          coverage: list[Coverage]) -> None:
        with self.db.write() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO observations(analysis_id, request_key, entity_id, track_key, proxy_frame_index, t_ms,"
                " width_px, height_px, x0, y0, x1, y1, centre_x_milli, centre_y_milli, mask_svg, mask_area_px,"
                " provider_confidence_bp, detection_state, identity_warning) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(analysis_id, o.request_key, o.entity_id, o.track_key, o.proxy_frame_index, o.t_ms, o.width_px, o.height_px,
                  *(o.box or (None, None, None, None)), *(o.centre_milli or (None, None)), o.mask_svg, o.mask_area_px,
                  o.provider_confidence_bp, o.detection_state, int(o.identity_warning)) for o in observations])
            for e in entities:
                conn.execute(
                    "INSERT OR REPLACE INTO entities(analysis_id, entity_id, kind, label, track_keys_json, first_t_ms, last_t_ms,"
                    " polygon_json, identity_warning) VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT polygon_json FROM entities WHERE"
                    " analysis_id = ? AND entity_id = ?), ?)",
                    (analysis_id, e.entity_id, e.kind, e.label, dumps(e.track_keys), e.first_t_ms, e.last_t_ms, analysis_id,
                     e.entity_id, int(e.identity_warning)))
            for c in coverage:
                conn.execute(
                    "INSERT OR REPLACE INTO coverage(analysis_id, request_key, concept, role, requested_start_frame,"
                    " requested_end_frame, terminal_status, missing_ranges_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (analysis_id, c.request_key, c.concept, c.role, c.requested_start_frame, c.requested_end_frame,
                     c.terminal_status, dumps(c.missing_ranges)))

    def save_zones(self, analysis_id: str, zones: list[Zone]) -> None:
        with self.db.write() as conn:
            for z in zones:
                conn.execute("INSERT OR REPLACE INTO entities(analysis_id, entity_id, kind, label, polygon_json) VALUES (?, ?, 'zone', ?, ?)",
                             (analysis_id, z.zone_id, z.label, dumps([list(p) for p in z.polygon])))

    def observations(self, analysis_id: str, *, entity_id: str | None = None, t0_ms: int | None = None,
                     t1_ms: int | None = None) -> list[Observation]:
        sql = "SELECT * FROM observations WHERE analysis_id = ?"
        params: list[Any] = [analysis_id]
        if entity_id is not None:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        if t0_ms is not None:
            sql += " AND t_ms >= ?"
            params.append(t0_ms)
        if t1_ms is not None:
            sql += " AND t_ms <= ?"
            params.append(t1_ms)
        sql += " ORDER BY t_ms, entity_id"
        out = []
        for r in self.db.query(sql, tuple(params)):
            box = None if r["x0"] is None else [r["x0"], r["y0"], r["x1"], r["y1"]]
            centre = None if r["centre_x_milli"] is None else (r["centre_x_milli"], r["centre_y_milli"])
            out.append(Observation(analysis_id=analysis_id, request_key=r["request_key"], entity_id=r["entity_id"],
                                   track_key=r["track_key"], proxy_frame_index=r["proxy_frame_index"], t_ms=r["t_ms"],
                                   width_px=r["width_px"], height_px=r["height_px"], box=box, centre_milli=centre,
                                   mask_svg=r["mask_svg"], mask_area_px=r["mask_area_px"],
                                   provider_confidence_bp=r["provider_confidence_bp"], detection_state=r["detection_state"],
                                   identity_warning=bool(r["identity_warning"])))
        return out

    def entities(self, analysis_id: str) -> list[Entity]:
        rows = self.db.query("SELECT * FROM entities WHERE analysis_id = ? AND kind != 'zone' ORDER BY entity_id", (analysis_id,))
        return [Entity(entity_id=r["entity_id"], kind=r["kind"], label=r["label"], track_keys=loads(r["track_keys_json"], []),
                       first_t_ms=r["first_t_ms"], last_t_ms=r["last_t_ms"], identity_warning=bool(r["identity_warning"]))
                for r in rows]

    def coverage(self, analysis_id: str) -> list[Coverage]:
        rows = self.db.query("SELECT * FROM coverage WHERE analysis_id = ? ORDER BY request_key", (analysis_id,))
        return [Coverage(request_key=r["request_key"], concept=r["concept"], role=r["role"],
                         requested_start_frame=r["requested_start_frame"], requested_end_frame=r["requested_end_frame"],
                         terminal_status=r["terminal_status"], missing_ranges=[tuple(x) for x in loads(r["missing_ranges_json"], [])])
                for r in rows]

    def save_candidates(self, analysis_id: str, candidates: list[Candidate]) -> None:
        with self.db.write() as conn:
            for c in candidates:
                conn.execute(
                    "INSERT OR REPLACE INTO candidates(analysis_id, candidate_id, candidate_type, object_id, t_start_ms, t_end_ms,"
                    " window_start_ms, window_end_ms, record_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (analysis_id, c.candidate_id, c.candidate_type, c.object_id, c.t_start_ms, c.t_end_ms,
                     c.window_start_ms, c.window_end_ms, c.model_dump_json(), utc_now()))

    def candidates(self, analysis_id: str) -> list[Candidate]:
        rows = self.db.query("SELECT record_json FROM candidates WHERE analysis_id = ? ORDER BY t_start_ms, candidate_id", (analysis_id,))
        return [Candidate.model_validate_json(r["record_json"]) for r in rows]

    def candidate(self, analysis_id: str, candidate_id: str) -> Candidate | None:
        row = self.db.one("SELECT record_json FROM candidates WHERE analysis_id = ? AND candidate_id = ?", (analysis_id, candidate_id))
        return Candidate.model_validate_json(row["record_json"]) if row else None

    def add_evidence(self, analysis_id: str, items: list[EvidenceItem]) -> None:
        with self.db.write() as conn:
            for item in items:
                conn.execute("INSERT INTO evidence(analysis_id, evidence_id, candidate_id, asset_id, record_json, created_at)"
                             " VALUES (?, ?, ?, ?, ?, ?)",
                             (analysis_id, item.evidence.evidence_id, item.candidate_id, item.asset_id, item.model_dump_json(), utc_now()))

    def evidence(self, analysis_id: str) -> dict[str, EvidenceItem]:
        rows = self.db.query("SELECT record_json FROM evidence WHERE analysis_id = ? ORDER BY created_at", (analysis_id,))
        items = [EvidenceItem.model_validate_json(r["record_json"]) for r in rows]
        return {i.evidence.evidence_id: i for i in items}

    def next_evidence_number(self, analysis_id: str) -> int:
        rows = self.db.query("SELECT evidence_id FROM evidence WHERE analysis_id = ?", (analysis_id,))
        return 1 + max((int(r["evidence_id"][1:]) for r in rows), default=0)

    def add_assessment(self, record: AssessmentRecord) -> None:
        with self.db.write() as conn:
            conn.execute("INSERT INTO assessments(id, analysis_id, candidate_id, purpose, model_id, run_mode, provider_call_id,"
                         " result_json, status, evidence_digests_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (record.assessment_id, record.analysis_id, record.candidate_id, record.purpose, record.model_id,
                          record.run_mode, record.provider_call_id, record.result.model_dump_json() if record.result else None,
                          record.status, dumps(record.evidence_digests), record.created_at))

    def assessments(self, analysis_id: str, candidate_id: str | None = None) -> list[AssessmentRecord]:
        sql, params = "SELECT * FROM assessments WHERE analysis_id = ?", [analysis_id]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        rows = self.db.query(sql + " ORDER BY created_at", tuple(params))
        return [AssessmentRecord(assessment_id=r["id"], analysis_id=r["analysis_id"], candidate_id=r["candidate_id"],
                                 purpose=r["purpose"], model_id=r["model_id"], run_mode=r["run_mode"], status=r["status"],
                                 result=loads(r["result_json"]) if r["result_json"] else None,
                                 evidence_digests=loads(r["evidence_digests_json"], []), provider_call_id=r["provider_call_id"],
                                 created_at=r["created_at"]) for r in rows]

    def append_event_revision(self, analysis_id: str, record: EventRecord, *, candidate_id: str | None,
                              basis: dict[str, Any]) -> EventRevision:
        digest = record_digest(record)
        now = utc_now()
        with self.db.write() as conn:
            current = conn.execute("SELECT MAX(revision) AS r FROM event_revisions WHERE analysis_id = ? AND event_id = ?",
                                   (analysis_id, record.event_id)).fetchone()["r"] or 0
            if record.revision != current + 1:
                raise ValueError(f"STALE_EVENT_REVISION: expected {current + 1}, got {record.revision}")
            conn.execute("INSERT INTO event_revisions(analysis_id, event_id, revision, candidate_id, record_json, basis_json,"
                         " record_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (analysis_id, record.event_id, record.revision, candidate_id, record.model_dump_json(), dumps(basis),
                          digest, now))
        return EventRevision(record=record, candidate_id=candidate_id, basis=basis, record_sha256=digest, created_at=now)

    def _revision(self, row: Any) -> EventRevision:
        return EventRevision(record=EventRecord.model_validate_json(row["record_json"]), candidate_id=row["candidate_id"],
                             basis=loads(row["basis_json"], {}), record_sha256=row["record_sha256"], created_at=row["created_at"])

    def event_history(self, analysis_id: str, event_id: str) -> list[EventRevision]:
        rows = self.db.query("SELECT * FROM event_revisions WHERE analysis_id = ? AND event_id = ? ORDER BY revision",
                             (analysis_id, event_id))
        return [self._revision(r) for r in rows]

    def current_events(self, analysis_id: str) -> dict[str, EventRevision]:
        rows = self.db.query(
            "SELECT e.* FROM event_revisions e JOIN (SELECT event_id, MAX(revision) AS r FROM event_revisions WHERE analysis_id = ?"
            " GROUP BY event_id) m ON e.event_id = m.event_id AND e.revision = m.r WHERE e.analysis_id = ? ORDER BY e.event_id",
            (analysis_id, analysis_id))
        revisions = [self._revision(r) for r in rows]
        return {r.record.event_id: r for r in sorted(revisions, key=lambda r: int(r.record.event_id[1:]))}

    def events_at(self, analysis_id: str, snapshot: dict[str, int]) -> dict[str, EventRevision]:
        out: dict[str, EventRevision] = {}
        for event_id, revision in snapshot.items():
            row = self.db.one("SELECT * FROM event_revisions WHERE analysis_id = ? AND event_id = ? AND revision = ?",
                              (analysis_id, event_id, revision))
            if row is None:
                raise ValueError("SNAPSHOT_REVISION_MISSING")
            out[event_id] = self._revision(row)
        return out

    def snapshot(self, analysis_id: str) -> dict[str, Any]:
        current = self.current_events(analysis_id)
        revisions = {k: v.record.revision for k, v in current.items()}
        digest = hashlib.sha256(canonical_bytes({k: [v.record.revision, v.record_sha256] for k, v in current.items()})).hexdigest()
        return {"event_revisions": revisions, "digest": digest, "taken_at": utc_now()}

    def next_event_number(self, analysis_id: str) -> int:
        rows = self.db.query("SELECT DISTINCT event_id FROM event_revisions WHERE analysis_id = ?", (analysis_id,))
        return 1 + max((int(r["event_id"][1:]) for r in rows), default=0)
