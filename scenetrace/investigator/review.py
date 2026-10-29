"""Optional secondary review in a fresh context. It can block claims; it can never promote one.
A missing or broken reviewer is reported as not_run or unavailable, never as passed."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain import Claim
from ..engine.candidates import assessment_cue_table
from ..providers.base import ProviderError, ProviderSet, RunContext
from ..providers.meta_muse import load_frames
from ..providers.schemas import StructuredOutputError
from ..storage import Storage
from ..store import AnalysisStore


@dataclass
class ReviewOutcome:
    mode: str = "not_run"
    verdict: str = "not_run"
    findings: list[dict[str, Any]] = field(default_factory=list)
    blocked: set[str] = field(default_factory=set)
    needs_evidence: set[str] = field(default_factory=set)


def run_review(ctx: RunContext, *, providers: ProviderSet, store: AnalysisStore, storage: Storage, analysis_id: str,
               owner_id: str, question: str, claims: list[Claim], enabled: bool) -> ReviewOutcome:
    if not enabled or not claims or providers.reviewer is None or providers.review_mode == "not_run":
        return ReviewOutcome()
    mode = providers.review_mode
    events = store.current_events(analysis_id)
    items = store.evidence(analysis_id)
    observations = store.observations(analysis_id)
    raw, cues = [], []
    for claim in claims:
        revision = events.get(claim.event_id)
        if revision is None:
            continue
        raw += [items[v] for v in revision.record.evidence_ids if v in items and not items[v].evidence.derived]
        candidate = store.candidate(analysis_id, revision.candidate_id) if revision.candidate_id else None
        if candidate is not None:
            cues.append({"event_id": claim.event_id, **assessment_cue_table(candidate, observations)})
    raw = sorted({i.evidence.evidence_id: i for i in raw}.values(), key=lambda i: i.evidence.t_ms)[:6]
    try:
        ctx.checkpoint()
        audit = providers.reviewer.review(ctx, question=question, claims=[c.model_dump() for c in claims], evidence=raw,
                                          frame_bytes=load_frames(storage, owner_id, raw) if mode == "image_secondary" else {},
                                          cues=cues, mode=mode)  # type: ignore[arg-type]
    except (ProviderError, StructuredOutputError):
        return ReviewOutcome(mode=mode, verdict="unavailable")
    claim_ids = {c.claim_id for c in claims}
    shown = {i.evidence.evidence_id for i in raw}
    if any(f.claim_id not in claim_ids or not set(f.evidence_ids) <= shown for f in audit.findings):
        return ReviewOutcome(mode=mode, verdict="unavailable")  # unknown IDs make the review invalid, not a pass
    by_claim = {f.claim_id: f for f in audit.findings}
    outcome = ReviewOutcome(mode=mode, findings=[f.model_dump() for f in audit.findings])
    for claim_id in claim_ids:
        finding = by_claim.get(claim_id)
        if finding is None or finding.verdict == "needs_evidence":
            outcome.needs_evidence.add(claim_id)
        elif finding.verdict == "reject":
            outcome.blocked.add(claim_id)
    verdicts = {by_claim[c].verdict if c in by_claim else "needs_evidence" for c in claim_ids}
    outcome.verdict = verdicts.pop() if len(verdicts) == 1 else "mixed"
    return outcome
