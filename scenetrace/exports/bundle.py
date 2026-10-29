"""Sealed, content-addressed export bundles.

A bundle is built in a temporary directory from a frozen snapshot of a completed
analysis, sealed with reference_core.integrity, verified, then renamed into place.
The trusted expected head is stored in the database registry, outside every
downloadable bundle. Integrity is not authenticity: a valid seal says the files
match their recorded hashes, not that the video or its interpretation is true.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

from reference_core.integrity import seal_bundle, verify_bundle

from ..db import loads
from ..engine.config import DEFAULT_ENGINE_CONFIG
from ..ids import utc_now
from ..jobs.context import Services
from ..providers.base import CancelToken

README = """SceneTrace evidence bundle

Verify it with the trusted head obtained separately from SceneTrace (not from this archive):

    python -m reference_core.integrity <extracted-directory> --expected-head <64-hex-digest>

Extract archives into a new, empty directory first. A valid result means the files match the hashes
sealed into this bundle and, when anchored, that the bundle is the one SceneTrace registered. It does
not prove that the original video is genuine or that any interpretation of it is correct.
"""
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_export(svc: Services, job: dict, cancel: CancelToken) -> str:
    export = dict(svc.db.require("SELECT * FROM exports WHERE id = ?", (job["export_id"],), "export"))
    analysis = svc.store.analysis(export["analysis_id"])
    clip = svc.store.clip(analysis["clip_id"])
    owner = clip["owner_id"]
    with svc.db.write() as conn:
        conn.execute("UPDATE exports SET status = 'building' WHERE id = ?", (export["id"],))
    snapshot = svc.store.snapshot(analysis["id"])
    staging = svc.storage.tmp_dir()
    root = staging / "bundle"
    (root / "evidence").mkdir(parents=True)
    items = svc.store.evidence(analysis["id"])
    for evidence_id, item in sorted(items.items(), key=lambda kv: int(kv[0][1:])):
        cancel.check()
        src, _media = svc.storage.asset_path(item.asset_id, owner, verify_digest=True)
        shutil.copyfile(src, root / "evidence" / f"{evidence_id}.png")
    revisions = []
    payloads = []
    for event_id in svc.store.current_events(analysis["id"]):
        for rev in svc.store.event_history(analysis["id"], event_id):
            revisions.append({"record": rev.record.model_dump(), "record_sha256": rev.record_sha256, "basis": rev.basis,
                              "candidate_id": rev.candidate_id, "created_at": rev.created_at})
            raw = [v for v in rev.record.evidence_ids if v in items and not items[v].evidence.derived]
            payloads.append({"kind": "event_revision", "event_id": event_id, "revision": rev.record.revision,
                             "status": rev.record.status, "record_sha256": rev.record_sha256,
                             "evidence_paths": [f"evidence/{v}.png" for v in raw]})
    investigations = [{"investigation_id": r["id"], "question": r["question"], "status": r["status"], "run_mode": r["run_mode"],
                       "snapshot": loads(r["snapshot_json"], {}), "result": loads(r["result_json"], {}),
                       "review_mode": r["review_mode"], "review_verdict": r["review_verdict"], "completed_at": r["completed_at"]}
                      for r in svc.db.query("SELECT * FROM investigations WHERE analysis_id = ? AND status IN ('completed', 'partial')"
                                            " ORDER BY created_at", (analysis["id"],))]
    calls = [dict(r) for r in svc.db.query("SELECT id, operation, model_id, attempt, state, cost_status, reserved_microusd,"
                                           " actual_microusd, request_id, elapsed_ms, created_at FROM provider_calls"
                                           " WHERE analysis_id = ? ORDER BY created_at", (analysis["recorded_from"] or analysis["id"],))]
    _write_json(root / "records" / "events.json", revisions)
    _write_json(root / "records" / "candidates.json", [c.model_dump() for c in svc.store.candidates(analysis["id"])])
    _write_json(root / "records" / "assessments.json", [a.model_dump() for a in svc.store.assessments(analysis["id"])])
    _write_json(root / "records" / "entities.json", [e.model_dump() for e in svc.store.entities(analysis["id"])])
    _write_json(root / "records" / "coverage.json", [c.model_dump() for c in svc.store.coverage(analysis["id"])])
    _write_json(root / "records" / "evidence.json", [i.model_dump() for i in items.values()])
    _write_json(root / "records" / "investigations.json", investigations)
    _write_json(root / "records" / "usage.json", calls)
    shutil.copyfile(svc.storage.proxy_dir(owner, clip["id"]) / "frame_map.json", root / "records" / "frame_map.json")
    _write_json(root / "records" / "provenance.json", {
        "analysis_id": analysis["id"], "run_mode": analysis["run_mode"], "media_kind": clip["media_kind"],
        "source_sha256": clip["source_sha256"], "proxy_sha256": clip["proxy_sha256"], "models": loads(analysis["models_json"], {}),
        "parser_version": analysis["parser_version"], "preprocessing_version": analysis["preprocessing_version"],
        "engine_config_sha256": DEFAULT_ENGINE_CONFIG.digest(), "rate_card_revision": analysis["rate_card_revision"],
        "event_snapshot": snapshot["event_revisions"], "snapshot_digest": snapshot["digest"], "exported_at": utc_now(),
        "project": "SceneTrace, created by Isaac Oselukwue"})
    (root / "README.txt").write_text(README, encoding="utf-8")
    head = seal_bundle(root, payloads)
    check = verify_bundle(root, head)
    if check["integrity"] != "valid":
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("EXPORT_SELF_CHECK_FAILED")
    final = svc.storage.export_dir(owner, clip["id"], export["id"])
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(root, final)
    archive = final.parent / f"{export['id']}.zip"
    files = sorted(p for p in final.rglob("*") if p.is_file())
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(f"scenetrace-{export['id'][4:16]}/{path.relative_to(final).as_posix()}", date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    asset = svc.storage.register_asset(owner_id=owner, clip_id=clip["id"], analysis_id=analysis["id"], path=archive,
                                       kind="export_archive", media_type="application/zip", derived=True,
                                       public=owner == "public")
    shutil.rmtree(staging, ignore_errors=True)
    with svc.db.write() as conn:
        conn.execute("UPDATE exports SET status = 'sealed', rel_dir = ?, archive_asset_id = ?, head_digest = ?, file_count = ?,"
                     " bytes = ?, sealed_at = ? WHERE id = ?", (svc.storage.relative(final), asset, head, len(files),
                                                                 sum(p.stat().st_size for p in files), utc_now(), export["id"]))
        conn.execute("INSERT INTO trusted_heads(export_id, head_digest, recorded_at, source) VALUES (?, ?, ?, 'server_registry')",
                     (export["id"], head, utc_now()))
    return "completed"


def verify_export(svc: Services, export: dict, supplied_head: str | None) -> dict:
    trusted = svc.db.one("SELECT head_digest FROM trusted_heads WHERE export_id = ?", (export["id"],))
    expected = supplied_head or (trusted["head_digest"] if trusted else None)
    result = verify_bundle(svc.storage.contained(export["rel_dir"]), expected)
    result["anchor_source"] = "supplied" if supplied_head else ("registry" if trusted else "none")
    return result
