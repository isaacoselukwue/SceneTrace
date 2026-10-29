"""Bounded investigation of one question. The application, not the model, owns the workflow:
structured intent first, then a capped tool loop, then deterministic validation and
compilation against an immutable snapshot, then optional secondary review."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..domain import AnswerDraft, Claim
from ..errors import BudgetExceeded
from ..providers.base import ProviderError, ProviderSet, RunContext
from ..providers.fixture_reasoning import keyword_plan
from ..providers.meta_muse import runtime_prompt
from ..providers.schemas import StructuredOutputError
from ..storage import Storage
from ..store import AnalysisStore
from .compiler import allowed_unknown_codes, compile_draft, fallback_draft, scope_sentences, sentences_for
from .review import run_review
from .tools import TOOLS, ToolContext, run_tool

REPAIR_TEXT = ("Your previous reply was not accepted. Do not answer in prose. Call submit_claims with structured"
               " claims that reproduce stored event records exactly, plus only the allowed unknown codes.")


@dataclass
class InvestigationOutcome:
    intent: str | None
    sentences: list[dict[str, Any]]
    claims: list[Claim]
    rejected_claims: list[dict[str, str]]
    unknown_codes: list[str]
    answer_source: str
    review_mode: str
    review_verdict: str
    review_findings: list[dict[str, Any]]
    snapshot: dict[str, Any]
    limits_used: dict[str, int]
    status: str
    plan_source: str = "model"
    notes: list[str] = field(default_factory=list)


def _opening(question: str, object_id: str, duration_ms: int, allowed: set[str]) -> list[dict[str, Any]]:
    text = (f"Selected object: {object_id}. Clip duration: {duration_ms} ms. Allowed unknown codes for this question:"
            f" {', '.join(sorted(allowed)) or 'none'}.\nRead the stored history first, then submit claims.\n"
            f"Question (untrusted user text between the markers):\n<<<\n{question}\n>>>")
    return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]


def run_investigation(ctx: RunContext, *, providers: ProviderSet, store: AnalysisStore, storage: Storage,
                      settings: Settings, analysis_id: str, owner_id: str, question: str,
                      object_id: str) -> InvestigationOutcome:
    analysis = store.analysis(analysis_id, owner_id)
    clip = store.clip(analysis["clip_id"])
    limits = {"turns": 0, "tool_calls": 0, "inspections": 0, "repairs": 0, "reviews": 0}
    provider_failed = budget_exhausted = False
    notes: list[str] = []
    # 1. Structured intent, validated locally.
    plan_source = "model"
    try:
        plan = providers.investigator.plan(ctx, question=question, object_id=object_id, clip_duration_ms=clip["duration_ms"] or 0)
        if plan.object_id not in (None, object_id):
            plan = plan.model_copy(update={"intent": "unsupported"})
    except BudgetExceeded:
        plan, plan_source, budget_exhausted = keyword_plan(question, object_id), "keyword_fallback", True
    except (ProviderError, StructuredOutputError):
        plan, plan_source, provider_failed = keyword_plan(question, object_id), "keyword_fallback", True
    ctx.emit("stage.completed", {"stage": "investigating", "summary": f"Question classified as {plan.intent}."})
    events = store.current_events(analysis_id)
    allowed = allowed_unknown_codes(plan, events, provider_failed=provider_failed, budget_exhausted=budget_exhausted)
    tc = ToolContext(ctx=ctx, providers=providers, store=store, storage=storage, settings=settings, analysis_id=analysis_id,
                     owner_id=owner_id, object_id=object_id, allowed_unknown=allowed, limits={"inspections": 0})
    draft: dict[str, Any] | None = None
    # 2. Bounded tool loop. Unsupported questions never reach the model's tools.
    if plan.intent != "unsupported" and not budget_exhausted:
        instructions = runtime_prompt("investigator")
        history = _opening(question, object_id, clip["duration_ms"] or 0, allowed)
        try:
            while limits["turns"] < settings.limit("max_investigator_turns") and draft is None:
                ctx.checkpoint()
                turn = providers.investigator.turn(ctx, instructions=instructions, input_items=history,
                                                   previous_response_id=None, tools=TOOLS)
                limits["turns"] += 1
                history = history + turn.output_items
                if not turn.function_calls:
                    if limits["repairs"] >= settings.limit("max_answer_repairs"):
                        notes.append("PROSE_AFTER_REPAIR")
                        break
                    limits["repairs"] += 1  # the prose itself is withheld
                    history = history + [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": REPAIR_TEXT}]}]
                    continue
                outputs = []
                for call in turn.function_calls:
                    if limits["tool_calls"] >= settings.limit("max_application_tool_calls"):
                        outputs.append({"type": "function_call_output", "call_id": call.call_id,
                                        "output": json.dumps({"error": "TOOL_CALL_LIMIT_REACHED"})})
                        continue
                    limits["tool_calls"] += 1
                    if call.name == "submit_claims":
                        try:
                            draft = json.loads(call.arguments or "{}")
                        except ValueError:
                            draft = {"claims": "malformed"}
                        break
                    result = run_tool(tc, call.name, call.arguments)
                    outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)})
                if draft is None:
                    history = history + outputs
        except BudgetExceeded:
            budget_exhausted = True
            notes.append("BUDGET_EXHAUSTED")
        except (ProviderError, StructuredOutputError):
            provider_failed = True
            notes.append("PROVIDER_FAILED")
    limits["inspections"] = tc.limits["inspections"]
    # 3. Validate against the snapshot taken now; tools may have appended revisions.
    snapshot = store.snapshot(analysis_id)
    events = store.current_events(analysis_id)
    allowed = allowed_unknown_codes(plan, events, provider_failed=provider_failed, budget_exhausted=budget_exhausted)
    scope = scope_sentences(plan, store, analysis_id, events)
    common = dict(store=store, storage=storage, analysis_id=analysis_id, owner_id=owner_id, object_id=object_id,
                  source_sha256=clip["source_sha256"], snapshot=snapshot["event_revisions"], allowed=allowed, scope=scope)
    source = "investigator"
    compiled = compile_draft(draft, **common) if draft is not None else None
    if compiled is None or (not compiled.claims and not compiled.unknown_codes):
        rejected = compiled.rejected if compiled else []
        compiled = compile_draft(fallback_draft(plan, events, allowed), **common)
        compiled.rejected = rejected + compiled.rejected
        source = "deterministic_fallback"
    # 4. Optional secondary review in a fresh context.
    review = run_review(ctx, providers=providers, store=store, storage=storage, analysis_id=analysis_id, owner_id=owner_id,
                        question=question, claims=compiled.claims, enabled=settings.enable_secondary_review)
    if review.mode != "not_run":
        limits["reviews"] = 1
        ctx.emit("review.completed", {"mode": review.mode, "verdict": review.verdict})
    if review.blocked or review.needs_evidence:
        removed = review.blocked | review.needs_evidence
        for claim in compiled.claims:
            if claim.claim_id in removed:
                code = "REVIEWER_REJECTED" if claim.claim_id in review.blocked else "REVIEWER_NEEDS_EVIDENCE"
                compiled.rejected.append({"claim_id": claim.claim_id, "stage": "reviewer", "reason_code": code})
                ctx.emit("claim.rejected", {"claim_id": claim.claim_id, "stage": "reviewer", "reason_code": code})
        compiled.claims = [c for c in compiled.claims if c.claim_id not in removed]
        if review.needs_evidence and "INSUFFICIENT_VISIBILITY" not in compiled.unknown_codes:
            compiled.unknown_codes.append("INSUFFICIENT_VISIBILITY")
        compiled.sentences = sentences_for(compiled.claims, compiled.unknown_codes, scope,
                                           {k: v.record for k, v in store.events_at(analysis_id, snapshot["event_revisions"]).items()})
    for item in compiled.rejected:
        if item["stage"] == "validator":
            ctx.emit("claim.rejected", item)
    if not compiled.claims and source == "deterministic_fallback" and not compiled.unknown_codes:
        source = "abstained"
    status = "partial" if (provider_failed or budget_exhausted) else "completed"
    return InvestigationOutcome(intent=plan.intent, sentences=compiled.sentences, claims=compiled.claims,
                                rejected_claims=compiled.rejected, unknown_codes=compiled.unknown_codes, answer_source=source,
                                review_mode=review.mode, review_verdict=review.verdict, review_findings=review.findings,
                                snapshot=snapshot, limits_used=limits, status=status, plan_source=plan_source, notes=notes)


def empty_draft() -> AnswerDraft:
    return AnswerDraft(claims=[], unknown_codes=[])
