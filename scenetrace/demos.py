"""Approved recorded demonstrations.

publish_demo packages a completed live analysis: the original upload, its proxy and
frame map, the transport recording of every provider exchange, the target and the
questions asked. import_demos replays that package through the normal pipeline in
recorded_real mode, so the public copy is rebuilt from recorded model outputs with
no network access and no new model calls. Only a human-approved package is published.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .db import dumps, loads
from .errors import AppError
from .ids import new_id, utc_now
from .jobs.context import Services
from .jobs.pipeline import new_investigation, run_analysis, run_question
from .providers.base import CancelToken
from .storage import sha256_file

PUBLIC = "public"


def publish_demo(svc: Services, analysis_id: str, *, title: str, description: str, approved_by: str, approval_note: str,
                 out_dir: Path) -> Path:
    analysis = svc.store.analysis(analysis_id)
    if analysis["run_mode"] != "live" or analysis["status"] not in ("completed", "partial"):
        raise AppError("NOT_PUBLISHABLE", "Only a finished live analysis can become a recorded demonstration.", status_code=409)
    clip = svc.store.clip(analysis["clip_id"])
    reused = loads(analysis["timings_json"], {}).get("tracking_reused_requests", 0)
    if reused:
        raise AppError("NOT_PUBLISHABLE", "Tracking was reused from another run; record a fresh live run to publish.", status_code=409)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "demo"
    target = out_dir / slug
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    owner = clip["owner_id"]
    shutil.copyfile(svc.storage.source_dir(owner, clip["id"]) / "source.media", target / "source.media")
    shutil.copyfile(svc.storage.proxy_dir(owner, clip["id"]) / "proxy.mp4", target / "proxy.mp4")
    shutil.copyfile(svc.storage.proxy_dir(owner, clip["id"]) / "frame_map.json", target / "frame_map.json")
    shutil.copytree(svc.storage.analysis_dir(owner, clip["id"], analysis_id) / "recording", target / "recording")
    questions = [r["question"] for r in svc.db.query(
        "SELECT question FROM investigations WHERE analysis_id = ? AND status IN ('completed', 'partial') ORDER BY created_at",
        (analysis_id,))]
    manifest = {
        "schema_version": 1, "title": title, "description": description, "approved_by": approved_by,
        "approval_note": approval_note, "display_name": clip["display_name"], "media_kind": clip["media_kind"],
        "source_sha256": clip["source_sha256"], "proxy_sha256": clip["proxy_sha256"], "recorded_at": analysis["completed_at"],
        "models": loads(analysis["models_json"], {}), "target": loads(analysis["target_json"], {}),
        "zones": loads(analysis["zones_json"], []), "questions": questions,
        "clip": {k: clip[k] for k in ("source_bytes", "source_container", "source_codec", "source_width", "source_height",
                                      "rotation_deg", "source_fps_num", "source_fps_den", "duration_ms", "proxy_width",
                                      "proxy_height", "proxy_fps", "proxy_frame_count", "frame_map_sha256",
                                      "preprocessing_version")},
    }
    (target / "demo.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _import_one(svc: Services, package: Path) -> str:
    manifest = json.loads((package / "demo.json").read_text(encoding="utf-8"))
    if sha256_file(package / "source.media") != manifest["source_sha256"] or sha256_file(package / "proxy.mp4") != manifest["proxy_sha256"]:
        raise AppError("DEMO_CORRUPT", f"{package.name}: media digests do not match the manifest.")
    existing = svc.db.one("SELECT d.id FROM demos d JOIN analyses a ON a.id = d.analysis_id JOIN clips c ON c.id = a.clip_id"
                          " WHERE d.title = ? AND c.source_sha256 = ?", (manifest["title"], manifest["source_sha256"]))
    if existing is not None:
        return str(existing["id"])
    clip_id, analysis_id, demo_id = new_id("clip"), new_id("ana"), new_id("demo")
    source_dir = svc.storage.source_dir(PUBLIC, clip_id)
    proxy_dir = svc.storage.proxy_dir(PUBLIC, clip_id)
    source_dir.mkdir(parents=True)
    proxy_dir.mkdir(parents=True)
    shutil.copyfile(package / "source.media", source_dir / "source.media")
    shutil.copyfile(package / "proxy.mp4", proxy_dir / "proxy.mp4")
    shutil.copyfile(package / "frame_map.json", proxy_dir / "frame_map.json")
    c = manifest["clip"]
    with svc.db.write() as conn:
        conn.execute(
            "INSERT INTO clips(id, owner_id, status, media_kind, display_name, consent_ack, source_sha256, source_bytes,"
            " source_container, source_codec, source_width, source_height, rotation_deg, source_fps_num, source_fps_den,"
            " duration_ms, proxy_sha256, proxy_width, proxy_height, proxy_fps, proxy_frame_count, frame_map_sha256,"
            " preprocessing_version, demo_id, created_at) VALUES (?, ?, 'ready', ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?)",
            (clip_id, PUBLIC, manifest["media_kind"], manifest["display_name"], manifest["source_sha256"], c["source_bytes"],
             c["source_container"], c["source_codec"], c["source_width"], c["source_height"], c["rotation_deg"],
             c["source_fps_num"], c["source_fps_den"], c["duration_ms"], manifest["proxy_sha256"], c["proxy_width"],
             c["proxy_height"], c["proxy_fps"], c["proxy_frame_count"], c["frame_map_sha256"], c["preprocessing_version"],
             demo_id, utc_now()))
        conn.execute("INSERT INTO analyses(id, owner_id, clip_id, run_mode, status, target_json, zones_json, models_json,"
                     " recorded_at, demo_id, created_at) VALUES (?, ?, ?, 'recorded_real', 'queued', ?, ?, ?, ?, ?, ?)",
                     (analysis_id, PUBLIC, clip_id, dumps(manifest["target"]), dumps(manifest["zones"]), dumps(manifest["models"]),
                      manifest["recorded_at"], demo_id, utc_now()))
    svc.storage.register_asset(owner_id=PUBLIC, clip_id=clip_id, path=proxy_dir / "proxy.mp4", kind="proxy",
                               media_type="video/mp4", derived=True)
    recording = svc.storage.analysis_dir(PUBLIC, clip_id, analysis_id) / "recording"
    shutil.copytree(package / "recording", recording)
    job = _job(svc, "analysis", analysis_id=analysis_id, clip_id=clip_id)
    state = run_analysis(svc, job, CancelToken())
    _close(svc, job, state)
    if state not in ("completed", "partial"):
        raise AppError("DEMO_REPLAY_FAILED", f"{package.name}: the recorded analysis did not replay ({state}).")
    for question in manifest["questions"]:
        inv_id = new_investigation(svc, owner_id=PUBLIC, analysis_id=analysis_id, question=question, object_id="O1",
                                   run_mode="recorded_real")
        with svc.db.write() as conn:
            conn.execute("UPDATE investigations SET recorded_at = ? WHERE id = ?", (manifest["recorded_at"], inv_id))
        qjob = _job(svc, "question", analysis_id=analysis_id, investigation_id=inv_id)
        _close(svc, qjob, run_question(svc, qjob, CancelToken()))
    with svc.db.write() as conn:
        conn.execute("INSERT INTO demos(id, analysis_id, title, description, approved_by, approval_note, published_at, rel_dir)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (demo_id, analysis_id, manifest["title"], manifest["description"],
                                                         manifest["approved_by"], manifest["approval_note"], utc_now(),
                                                         svc.storage.relative(svc.storage.clip_dir(PUBLIC, clip_id))))
    return demo_id


def _job(svc: Services, kind: str, **links: str) -> dict:
    job_id = new_id("job")
    with svc.db.write() as conn:
        conn.execute("INSERT INTO jobs(id, owner_id, kind, clip_id, analysis_id, investigation_id, state, run_mode, created_at,"
                     " started_at) VALUES (?, ?, ?, ?, ?, ?, 'running', 'recorded_real', ?, ?)",
                     (job_id, PUBLIC, kind, links.get("clip_id"), links.get("analysis_id"), links.get("investigation_id"),
                      utc_now(), utc_now()))
    return dict(svc.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


def _close(svc: Services, job: dict, state: str) -> None:
    with svc.db.write() as conn:
        conn.execute("UPDATE jobs SET state = ?, finished_at = ? WHERE id = ?", (state, utc_now(), job["id"]))
    svc.events.append(job["id"], f"job.{'completed' if state == 'completed' else 'partial' if state == 'partial' else 'failed'}",
                      {"summary": "Recorded run replayed without model calls."}, mode="recorded_real")


def import_demos(svc: Services, source: Path) -> list[str]:
    imported = []
    for package in sorted(p for p in source.iterdir() if (p / "demo.json").is_file()):
        started = time.monotonic()
        imported.append(_import_one(svc, package))
        print(f"imported {package.name} in {time.monotonic() - started:.1f}s")
    return imported
