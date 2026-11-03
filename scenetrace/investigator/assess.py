from __future__ import annotations

from typing import Any, cast

from ..config import Settings
from ..domain import AssessmentRecord, AssessmentResult, Candidate, EvidenceItem
from ..engine.candidates import assessment_cue_table
from ..ids import new_id, utc_now
from ..providers.base import ProviderError, ProviderSet, RunContext
from ..providers.meta_muse import load_frames
from ..providers.schemas import StructuredOutputError
from ..storage import Storage
from ..store import AnalysisStore


def candidate_frames(store: AnalysisStore, analysis_id: str, candidate_id: str, *, latest: int = 6) -> list[EvidenceItem]:
    raw = [e for e in store.evidence(analysis_id).values() if e.candidate_id == candidate_id and not e.evidence.derived]
    raw.sort(key=lambda e: int(e.evidence.evidence_id[1:]))
    chosen = raw[-latest:]
    return sorted(chosen, key=lambda e: e.evidence.t_ms)


def semantic_problems(result: AssessmentResult, candidate: Candidate, frames: list[EvidenceItem],
                      persons: set[str], zones: set[str]) -> list[str]:
    problems = []
    if result.candidate_id != candidate.candidate_id or result.object_id != candidate.object_id:
        problems.append("WRONG_CANDIDATE")
    if not set(result.evidence_ids) <= {f.evidence.evidence_id for f in frames}:
        problems.append("UNKNOWN_EVIDENCE_ID")
    named = set(result.actor_candidates) | ({result.actor_assignment} if result.actor_assignment else set())
    if not named <= persons:
        problems.append("UNKNOWN_ACTOR_ID")
    if result.actor_assignment and result.actor_assignment not in result.actor_candidates:
        problems.append("ACTOR_NOT_IN_CANDIDATES")
    if result.destination_zone and result.destination_zone not in zones:
        problems.append("UNKNOWN_ZONE_ID")
    return problems


def assess_candidate(ctx: RunContext, *, providers: ProviderSet, store: AnalysisStore, storage: Storage,
                     settings: Settings, analysis_id: str, owner_id: str, candidate: Candidate,
                     purpose: str = "initial") -> AssessmentRecord:
    frames = candidate_frames(store, analysis_id, candidate.candidate_id)
    observations = store.observations(analysis_id)
    persons = {e.entity_id for e in store.entities(analysis_id) if e.kind == "person"}
    zones = {z.zone_id for z in store.zones(analysis_id)}
    model_id = providers.assessor.model_id
    status, result = "skipped", None
    if frames:
        ctx.checkpoint()
        frame_bytes = load_frames(storage, owner_id, frames)
        try:
            result = providers.assessor.assess(ctx, candidate=candidate, frames=frames, frame_bytes=frame_bytes,
                                               cue_table=assessment_cue_table(candidate, observations), purpose=purpose)
            status = "completed"
            if semantic_problems(result, candidate, frames, persons, zones):
                status, result = "invalid", None
        except StructuredOutputError:
            status, result = "invalid", None
        except ProviderError:
            status, result = "failed", None
    record = AssessmentRecord(assessment_id=new_id("req"), analysis_id=analysis_id, candidate_id=candidate.candidate_id,
                              purpose=cast(Any, purpose), model_id=model_id, run_mode=ctx.run_mode, status=status, result=result,
                              evidence_digests=[f.evidence.asset_sha256 for f in frames], provider_call_id=None,
                              created_at=utc_now())
    store.add_assessment(record)
    return record
