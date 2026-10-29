"""Evaluation reports: python -m scenetrace.evaluation.run [--live-review]

Three separate results, never merged into one headline:
1. Real-video evaluation over owned, pre-labelled clips in eval/labels/ (held-out split only).
2. A synthetic smoke check over the drawn test clips. Not real-video evidence.
3. Planted-fault and clean-control cases from recorded real run records.

Recorded demonstrations are replayed into a throwaway data directory, so the
default run needs no key and makes no provider calls. --live-review additionally
sends every fault case to the live secondary reviewer (spends money within caps).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import ROOT, Settings
from ..db import loads
from ..ids import new_id, utc_now
from ..jobs.context import Services
from ..providers.base import ProviderSet, RunContext
from .faults import append_revisions, build_cases, validator_verdict
from .matching import ACTIONS, Interval, actor_scores, event_scores, match, ratio

REPORTS = ROOT / "reports"
LABELS = ROOT / "eval" / "labels"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def real_video(svc: Services) -> dict:
    labels = sorted(p for p in LABELS.glob("*.label.json")) if LABELS.is_dir() else []
    held_out = [json.loads(p.read_text()) for p in labels]
    held_out = [x for x in held_out if x.get("split") == "held_out" and x.get("media_kind") == "user_upload"]
    if not held_out:
        return {"status": "not_measured", "real_clips_evaluated": 0, "held_out_labels_found": 0,
                "metrics": {"action_event_f1": None, "actor_accuracy": None, "unsupported_assertion_rate": None,
                            "useful_answer_coverage": None, "median_cold_ms": None, "median_warm_ms": None,
                            "median_analysis_microusd": None},
                "note": "No owned, consented, labelled real clips exist yet. Real-video performance is not measured."}
    clips = [score_clip(svc, label) for label in held_out]
    return {"status": "measured", "real_clips_evaluated": sum(1 for c in clips if c["completed"]),
            "held_out_labels_found": len(held_out), "clips": clips, "pooled": pool(clips)}


def _analysis_for(svc: Services, source_sha256: str) -> dict | None:
    row = svc.db.one("SELECT a.* FROM analyses a JOIN clips c ON c.id = a.clip_id WHERE c.source_sha256 = ? AND"
                     " a.status IN ('completed', 'partial') AND a.run_mode != 'fixture' ORDER BY a.created_at DESC LIMIT 1",
                     (source_sha256,))
    return dict(row) if row else None


def associate(svc: Services, analysis_id: str, actors: list[dict]) -> dict[str, str]:
    """Explicit evaluation association: each P track maps to the labelled actor whose presence overlaps it most."""
    mapping: dict[str, str] = {}
    for entity in svc.store.entities(analysis_id):
        if entity.kind != "person" or entity.first_t_ms is None or entity.last_t_ms is None:
            continue
        overlaps = [(min(entity.last_t_ms, a["present_ms"][1]) - max(entity.first_t_ms, a["present_ms"][0]), a["actor"]) for a in actors]
        best = max(overlaps, default=(0, None))
        if best[0] > 0 and best[1]:
            mapping[entity.entity_id] = best[1]
    return mapping


def score_clip(svc: Services, label: dict) -> dict:
    analysis = _analysis_for(svc, label["source_sha256"])
    if analysis is None:
        return {"clip_id": label["clip_id"], "completed": False}
    events = svc.store.current_events(analysis["id"])
    expected = [Interval(f"L{i}", e["event_type"], e["t_start_ms"], e["t_end_ms"], e.get("actor"))
                for i, e in enumerate(label["events"], start=1) if e["event_type"] in ACTIONS and e.get("observable", True)]
    unobservable = [e for e in label["events"] if e["event_type"] in ACTIONS and not e.get("observable", True)]
    full = [Interval(r.record.event_id, r.record.event_type, r.record.t_start_ms, r.record.t_end_ms, r.record.actor_id)
            for r in events.values() if r.record.event_type in ACTIONS and r.record.status == "supported"]
    geometry = [Interval(c.candidate_id, c.candidate_type, c.t_start_ms, c.t_end_ms,
                         c.actor_candidates[0] if len(c.actor_candidates) == 1 else None)
                for c in svc.store.candidates(analysis["id"])
                if c.candidate_type in ACTIONS and set(c.reason_codes) & {"TARGET_MOVED", "TARGET_LOST"}]
    association = associate(svc, analysis["id"], label.get("actors", []))
    full_scores = event_scores(full, expected)
    pairs = match(full, expected)
    questions = score_questions(svc, analysis["id"], label, association)
    return {"clip_id": label["clip_id"], "completed": True, "analysis_id": analysis["id"], "run_mode": analysis["run_mode"],
            "status": analysis["status"], "labelled_action_events": len(expected), "unobservable_labelled_events": len(unobservable),
            "full_system": full_scores, "geometry_only": event_scores(geometry, expected),
            "actor": actor_scores(pairs, association), "actor_association": association, "questions": questions}


def score_questions(svc: Services, analysis_id: str, label: dict, association: dict[str, str]) -> dict:
    asked = {r["question"]: dict(r) for r in svc.db.query("SELECT * FROM investigations WHERE analysis_id = ?", (analysis_id,))}
    rows = []
    for q in label.get("questions", []):
        inv = asked.get(q["question"])
        if inv is None:
            rows.append({"question": q["question"], "asked": False})
            continue
        result = loads(inv["result_json"], {}) or {}
        claims = result.get("claims", [])
        actions = [c for c in claims if c["predicate"] in ACTIONS and c["certainty"] == "supported"]
        abstained = not claims or (q["intent"] == "actor" and not any(c.get("actor_id") for c in actions))
        truth = [e for e in label["events"] if e["event_type"] in ACTIONS and e.get("observable", True)]
        if q["intent"] == "actor":
            named = {association.get(c["actor_id"]) for c in actions if c.get("actor_id")}
            correct = bool(truth) and named == {e["actor"] for e in truth} and not abstained
        elif q["intent"] == "interaction_window":
            correct = not actions and not truth if "between 00:04 and 00:08" in q["question"] else bool(actions)
            abstained = False
        else:
            correct = bool(truth) and len(match([Interval(c["claim_id"], c["predicate"], c["t_start_ms"], c["t_end_ms"])
                                                 for c in actions], [Interval(f"L{i}", e["event_type"], e["t_start_ms"],
                                                                              e["t_end_ms"]) for i, e in enumerate(truth)])) == len(truth)
        asserted_wrong = sum(1 for c in actions if association.get(c.get("actor_id") or "", "") not in
                             {e.get("actor") for e in truth} and c.get("actor_id"))
        rows.append({"question": q["question"], "asked": True, "observable": q["observable"], "answered_correctly": correct and q["observable"],
                     "abstained": abstained, "wrong_assertions": asserted_wrong, "sentences": [s["text"] for s in result.get("sentences", [])]})
    observable = [r for r in rows if r.get("asked") and r["observable"]]
    unobservable = [r for r in rows if r.get("asked") and not r["observable"]]
    return {"rows": rows, "observable_asked": len(observable), "useful_answers": sum(1 for r in observable if r["answered_correctly"]),
            "abstentions": sum(1 for r in rows if r.get("abstained")), "unobservable_asked": len(unobservable),
            "correct_abstentions": sum(1 for r in unobservable if r["abstained"])}


def pool(clips: list[dict]) -> dict:
    done = [c for c in clips if c.get("completed")]
    out: dict[str, Any] = {"clips": len(done)}
    for arm in ("full_system", "geometry_only"):
        tp, fp, fn = (sum(c[arm][k] for c in done) for k in ("tp", "fp", "fn"))
        out[arm] = {"tp": tp, "fp": fp, "fn": fn, "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
                    "f1": ratio(2 * tp, 2 * tp + fp + fn)}
    obs = sum(c["actor"]["observable_matched"] for c in done)
    correct = sum(c["actor"]["correct"] for c in done)
    assigned = sum(c["actor"]["assigned"] for c in done)
    out["actor"] = {"observable_matched": obs, "correct": correct, "assigned": assigned,
                    "wrong_confident": sum(c["actor"]["wrong_confident"] for c in done), "accuracy": ratio(correct, obs),
                    "coverage": ratio(assigned, obs)}
    q_obs = sum(c["questions"]["observable_asked"] for c in done)
    q_unobs = sum(c["questions"]["unobservable_asked"] for c in done)
    asked = sum(len([r for r in c["questions"]["rows"] if r.get("asked")]) for c in done)
    out["questions"] = {"asked": asked, "observable": q_obs, "useful_answers": sum(c["questions"]["useful_answers"] for c in done),
                        "useful_answer_coverage": ratio(sum(c["questions"]["useful_answers"] for c in done), q_obs),
                        "abstention_rate": ratio(sum(c["questions"]["abstentions"] for c in done), asked),
                        "unobservable": q_unobs, "correct_abstention": ratio(sum(c["questions"]["correct_abstentions"] for c in done), q_unobs)}
    return out


def synthetic_smoke(svc: Services) -> dict:
    labels = {}
    for path in (ROOT / "samples" / "synthetic").glob("*.label.json"):
        video = path.with_name(path.name.replace(".label.json", ".mp4"))
        labels[_sha(video)] = json.loads(path.read_text()) | {"source_sha256": _sha(video)}
    clips = [score_clip(svc, label) for label in labels.values()]
    return {"status": "measured", "media": "synthetic drawn animations rendered by tools/render_synthetic_clip.py",
            "not_real_video_evidence": True, "clips": clips, "pooled": pool(clips),
            "note": "Two drawn clips with ground truth taken from the renderer. A smoke check of the whole pipeline on"
                    " recorded live model outputs, not a benchmark. The development hero clip is included, so these numbers"
                    " say nothing about held-out performance."}


def fault_injection(svc: Services, live_review: bool) -> dict:
    demos = {r["title"]: dict(r) for r in svc.db.query("SELECT d.title, d.analysis_id FROM demos d")}
    pickup = next(v["analysis_id"] for k, v in demos.items() if "pickup" in k.lower())
    occlusion = next(v["analysis_id"] for k, v in demos.items() if "hidden" in k.lower())
    sources = {a: svc.store.clip(svc.store.analysis(a)["clip_id"])["source_sha256"] for a in (pickup, occlusion)}
    faults, clean = build_cases(svc.store, pickup, occlusion)
    results: list[dict[str, Any]] = []

    def check(case) -> None:
        blocked, code = validator_verdict(svc.store, case, "public", sources[case.analysis_id])
        results.append({"case_id": case.case_id, "category": case.category, "analysis_id": case.analysis_id, "note": case.note,
                        "claim": case.claim.model_dump(),
                        "validator_blocked": blocked, "validator_code": code})

    for case in faults + clean:
        check(case)
    stale, fresh = append_revisions(svc.store, pickup, occlusion)
    for case in stale + fresh:
        check(case)
    reviewer = {"status": "not_run", "note": "Run with --live-review to send every case to the live secondary reviewer."}
    if live_review:
        reviewer = review_cases(svc, results, sources)
    planted = [r for r in results if r["category"] != "clean"]
    controls = [r for r in results if r["category"] == "clean"]
    by_category = {c: {"planted": sum(1 for r in planted if r["category"] == c),
                       "validator_caught": sum(1 for r in planted if r["category"] == c and r["validator_blocked"])}
                   for c in sorted({str(r["category"]) for r in planted})}
    summary = {"planted": len(planted), "clean_controls": len(controls),
               "validator": {"caught": sum(1 for r in planted if r["validator_blocked"]),
                             "false_rejects": sum(1 for r in controls if r["validator_blocked"])},
               "by_category": by_category, "reviewer": reviewer}
    if live_review and reviewer.get("status") == "measured":
        summary["combined"] = {"caught": sum(1 for r in planted if r["validator_blocked"] or r.get("reviewer_blocked")),
                               "false_rejects": sum(1 for r in controls if r["validator_blocked"] or r.get("reviewer_blocked"))}
    return {"summary": summary, "cases": results,
            "note": "Mutations of claims about two recorded live runs of synthetic clips: not 30 independent scenes."}


def review_cases(svc: Services, results: list[dict], sources: dict[str, str]) -> dict:
    """Send each case's claim, alone, to the live reviewer with the cited event's evidence."""
    from ..domain import Claim
    from ..investigator.review import run_review
    from ..providers.meta_muse import MetaReviewer
    from ..providers.transport import MetaTransport
    settings = dataclasses.replace(svc.settings, enable_secondary_review=True)
    reviewer = MetaReviewer(transport=MetaTransport(settings.provider_base_url, settings.model_api_key), settings=settings)
    errors = 0
    for row in results:
        claim = Claim(**row["claim"])
        analysis_id = row["analysis_id"]
        ctx = RunContext(owner_id="public", scope="eval", run_mode="live", ledger=svc.ledger, analysis_id=analysis_id,
                         job_id=new_id("job"))
        providers = ProviderSet(run_mode="live", tracker=None, assessor=None, investigator=None, reviewer=reviewer,  # type: ignore[arg-type]
                                review_mode="image_secondary")
        outcome = run_review(ctx, providers=providers, store=svc.store, storage=svc.storage, analysis_id=analysis_id,
                             owner_id="public", question="What happened to the selected object?", claims=[claim], enabled=True)
        if outcome.verdict == "unavailable":
            errors += 1
        row["reviewer_verdict"] = outcome.verdict
        row["reviewer_blocked"] = bool(outcome.blocked or outcome.needs_evidence)
    planted = [r for r in results if r["category"] != "clean"]
    controls = [r for r in results if r["category"] == "clean"]
    return {"status": "measured", "model": settings.models["reviewer"], "mode": "image_secondary",
            "caught": sum(1 for r in planted if r["reviewer_blocked"]), "false_rejects": sum(1 for r in controls if r["reviewer_blocked"]),
            "unavailable_results": errors,
            "note": "needs_evidence counts as blocked, as in production. Reviewer-only figures deliberately bypass the validator."}


def cost_latency() -> dict:
    runs = [json.loads((p / "demo.json").read_text()) for p in sorted((ROOT / "demos").iterdir()) if (p / "demo.json").is_file()]
    cold_ms = [r["original_run"]["cold_timings_ms"].get("total_ms") for r in runs if r.get("original_run")]
    cold_cost = [r["original_run"]["cold_cost_microusd"] for r in runs if r.get("original_run")]
    warm = [q for r in runs for q in r.get("original_run", {}).get("questions", [])]

    def stats(values: list[int]) -> dict:
        values = [v for v in values if v is not None]
        return {"n": len(values), "median": statistics.median(values) if values else None,
                "min": min(values, default=None), "max": max(values, default=None)}
    return {"source": "original live runs of the two synthetic demo clips (recorded 29 October 2026)",
            "cold_total_ms": stats(cold_ms), "cold_cost_microusd": stats(cold_cost),
            "warm_question_ms": stats([q["question_ms"] for q in warm]),
            "warm_question_cost_microusd": stats([q["cost_microusd"] for q in warm]),
            "note": "Small n from synthetic clips. Costs are computed from provider-reported usage and the dated rate card."}


def write_reports(report: dict) -> None:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "evaluation.json").write_text(json.dumps({k: v for k, v in report.items() if k != "fault_injection"}, indent=2) + "\n")
    (REPORTS / "fault-injection.json").write_text(json.dumps(report["fault_injection"], indent=2) + "\n")
    with (REPORTS / "evaluation.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["section", "clip_id", "arm", "tp", "fp", "fn", "precision", "recall", "f1"])
        for section in ("real_video", "synthetic_smoke"):
            for clip in report[section].get("clips", []):
                for arm in ("full_system", "geometry_only"):
                    if clip.get("completed"):
                        s = clip[arm]
                        writer.writerow([section, clip["clip_id"], arm, s["tp"], s["fp"], s["fn"], s["precision"], s["recall"], s["f1"]])
    (REPORTS / "SUMMARY.md").write_text(summary_markdown(report))


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def summary_markdown(r: dict) -> str:
    s = r["synthetic_smoke"]["pooled"]
    f = r["fault_injection"]["summary"]
    c = r["cost_latency"]
    lines = [
        "# SceneTrace evaluation summary", "",
        f"Generated {r['generated_at']} from commit `{_fmt(r['software_commit'])}` with `make evaluate`.", "",
        "## Real-video evaluation", "",
        f"Status: **{r['real_video']['status']}**. Real clips evaluated: **{r['real_video']['real_clips_evaluated']}**.",
        r["real_video"].get("note", ""), "",
        "## Synthetic smoke check (not real-video evidence)", "",
        "Two drawn test animations, scored against the renderer's ground truth using recorded live model outputs.",
        "This checks the pipeline end to end; it is not a benchmark and includes the development hero clip.", "",
        "| Arm | TP | FP | FN | Precision | Recall | F1 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[f"| {arm.replace('_', ' ')} | {s[arm]['tp']} | {s[arm]['fp']} | {s[arm]['fn']} | {_fmt(s[arm]['precision'])} |"
          f" {_fmt(s[arm]['recall'])} | {_fmt(s[arm]['f1'])} |" for arm in ("full_system", "geometry_only")], "",
        f"Actor: {s['actor']['correct']} correct of {s['actor']['observable_matched']} matched observable events"
        f" ({s['actor']['wrong_confident']} wrong confident assignments).",
        f"Questions: {s['questions']['useful_answers']} useful answers of {s['questions']['observable']} observable questions;"
        f" {s['questions']['unobservable']} unobservable question(s), correct abstention {_fmt(s['questions']['correct_abstention'])};"
        f" abstention rate {_fmt(s['questions']['abstention_rate'])} over {s['questions']['asked']} asked.", "",
        "## Planted faults (validator)", "",
        f"{f['planted']} planted faults and {f['clean_controls']} clean controls built from the recorded real run records.",
        f"Deterministic validator: **{f['validator']['caught']}/{f['planted']} caught**, **{f['validator']['false_rejects']}/{f['clean_controls']} false rejects**.", "",
        "| Category | Planted | Validator caught |", "| --- | ---: | ---: |",
        *[f"| {k.replace('_', ' ')} | {v['planted']} | {v['validator_caught']} |" for k, v in f["by_category"].items()], "",
        f"Secondary reviewer alone: {f['reviewer'].get('status')}"
        + (f", {f['reviewer']['caught']}/{f['planted']} caught, {f['reviewer']['false_rejects']}/{f['clean_controls']} false rejects."
           if f["reviewer"].get("status") == "measured" else "."),
        *( [f"Combined: {f['combined']['caught']}/{f['planted']} caught, {f['combined']['false_rejects']}/{f['clean_controls']} false rejects."]
           if "combined" in f else []), "",
        "## Cost and latency (original live runs of the synthetic clips)", "",
        f"Cold analysis: n={c['cold_total_ms']['n']}, median {_fmt(c['cold_total_ms']['median'])} ms"
        f" (min {_fmt(c['cold_total_ms']['min'])}, max {_fmt(c['cold_total_ms']['max'])});"
        f" median cost {_fmt(c['cold_cost_microusd']['median'])} micro-USD.",
        f"Warm questions: n={c['warm_question_ms']['n']}, median {_fmt(c['warm_question_ms']['median'])} ms"
        f" (min {_fmt(c['warm_question_ms']['min'])}, max {_fmt(c['warm_question_ms']['max'])});"
        f" median cost {_fmt(c['warm_question_cost_microusd']['median'])} micro-USD.", "",
        "Failure example: in the recorded occlusion run the secondary reviewer asked for more evidence on the"
        " last-sighting claim, so \"Where was the bottle last seen?\" was answered only with uncertainty messages.", "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-review", action="store_true", help="Send fault cases to the live reviewer (spends money)")
    args = parser.parse_args(argv)
    from ..demos import import_demos
    with tempfile.TemporaryDirectory(prefix="scenetrace-eval-") as tmp:
        env = {"SCENETRACE_DATA_DIR": tmp}
        if not args.live_review:
            env["MODEL_API_KEY"] = ""
        settings = Settings.load(ROOT, env=env)
        svc = Services.create(settings)
        import_demos(svc, ROOT / "demos")
        report = {"generated_at": utc_now(), "software_commit": _commit(),
                  "config_sha256": _sha(ROOT / "config" / "defaults.json"),
                  "label_sha256": {p.name: _sha(p) for p in sorted((ROOT / "samples" / "synthetic").glob("*.label.json"))},
                  "real_video": real_video(svc), "synthetic_smoke": synthetic_smoke(svc),
                  "fault_injection": fault_injection(svc, args.live_review), "cost_latency": cost_latency(),
                  "arms": {"A_native_video_baseline": "not_run", "B_geometry_only": "scored offline from stored candidates",
                           "C_full_system": "scored from recorded live runs", "D_without_reviewer": "not_run"}}
        write_reports(report)
        print((REPORTS / "SUMMARY.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
