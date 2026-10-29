"""Provider protocols, run context and the metering wrapper.

Three implementations exist: the real Meta adapter (live), the recorded-result
reader (recorded_real) and the synthetic fixture adapter (fixture). Mode is
immutable for a run. Fixture never touches the network; replay never runs a
model; live never substitutes synthetic results after an error.

Every paid provider attempt goes through `metered_call`, which reserves the
worst-case cost in the durable ledger before dispatch and settles it afterwards.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..domain import (
    AssessmentResult,
    AuditResult,
    BoxPrompt,
    Candidate,
    ConceptPrompt,
    EvidenceItem,
    Plan,
    ProxyVideo,
    RunMode,
    SelectionCandidate,
    TrackingResult,
)
from ..errors import BudgetExceeded, Cancelled, DeadlineExceeded

Scope = Literal["cold", "warm", "eval", "spike", "cleanup"]


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


class Ledger(Protocol):
    """Durable reservation ledger (implemented in scenetrace/budget.py)."""

    def reserve(self, ctx: RunContext, *, operation: str, model_id: str, upper_bound_microusd: int,
                attempt: int, paid: bool = True) -> str: ...

    def mark_dispatched(self, call_id: str) -> None: ...

    def settle(self, call_id: str, *, actual_microusd: int | None, usage: dict | None, request_id: str | None,
               response_id: str | None, http_status: int | None, elapsed_ms: int | None,
               error_code: str | None = None) -> None: ...

    def release_unsent(self, call_id: str) -> None: ...

    def record_replay(self, ctx: RunContext, *, operation: str, model_id: str, original_call: dict) -> str: ...


@dataclass
class RunContext:
    owner_id: str
    scope: Scope
    run_mode: RunMode
    ledger: Ledger
    analysis_id: str | None = None
    investigation_id: str | None = None
    job_id: str | None = None
    deadline_monotonic: float | None = None
    cancel: CancelToken = field(default_factory=CancelToken)
    emit: Callable[[str, dict[str, Any]], None] = lambda event_type, payload: None
    recorder: Any = None  # providers.recording.Recorder when live calls are being recorded

    def checkpoint(self) -> None:
        """Call before scheduling any new provider or media work."""
        self.cancel.check()
        if self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic:
            raise DeadlineExceeded()

    def remaining_s(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return max(0.0, self.deadline_monotonic - time.monotonic())


class ProviderError(Exception):
    """A sanitised provider failure. `retryable` marks transient errors eligible for one retry."""

    def __init__(self, code: str, *, http_status: int | None = None, retryable: bool = False,
                 retry_after_s: float | None = None, request_id: str | None = None,
                 ambiguous: bool = False, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.request_id = request_id
        self.ambiguous = ambiguous  # provider work may have run and been billed
        self.detail = detail


class ProviderUnavailable(ProviderError):
    """Model access missing (403/404 model_not_found) or no key configured."""


class RecordingMiss(ProviderError):
    """Replay found no recorded result for a request. Replay never falls back to a model."""


@dataclass
class CallOutcome:
    value: Any
    usage: dict | None
    actual_microusd: int | None
    request_id: str | None
    response_id: str | None
    http_status: int | None


def metered_call(ctx: RunContext, *, operation: str, model_id: str, upper_bound_microusd: int,
                 dispatch: Callable[[], CallOutcome], attempt: int = 1, paid: bool = True) -> tuple[Any, str]:
    """Reserve, dispatch once, settle. Returns (value, provider_call_id).

    A BudgetExceeded from the ledger means no call was made. A timeout or
    ambiguous disconnect keeps the reservation with cost_status=unknown. This
    helper never retries; callers decide on at most one eligible retry, which is
    a new reservation and a new ledger entry.
    """
    ctx.checkpoint()
    call_id = ctx.ledger.reserve(ctx, operation=operation, model_id=model_id,
                                 upper_bound_microusd=upper_bound_microusd, attempt=attempt, paid=paid)
    started = time.monotonic()
    try:
        ctx.checkpoint()
    except BaseException:
        ctx.ledger.release_unsent(call_id)
        raise
    ctx.ledger.mark_dispatched(call_id)
    try:
        outcome = dispatch()
    except ProviderError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        if exc.ambiguous:
            ctx.ledger.settle(call_id, actual_microusd=None, usage=None, request_id=exc.request_id, response_id=None,
                              http_status=exc.http_status, elapsed_ms=elapsed, error_code=exc.code)
        else:
            # The provider rejected the request before doing billable work.
            ctx.ledger.settle(call_id, actual_microusd=0, usage=None, request_id=exc.request_id, response_id=None,
                              http_status=exc.http_status, elapsed_ms=elapsed, error_code=exc.code)
        raise
    except BaseException:
        elapsed = int((time.monotonic() - started) * 1000)
        ctx.ledger.settle(call_id, actual_microusd=None, usage=None, request_id=None, response_id=None,
                          http_status=None, elapsed_ms=elapsed, error_code="LOCAL_INTERRUPTION")
        raise
    elapsed = int((time.monotonic() - started) * 1000)
    ctx.ledger.settle(call_id, actual_microusd=outcome.actual_microusd, usage=outcome.usage,
                      request_id=outcome.request_id, response_id=outcome.response_id,
                      http_status=outcome.http_status, elapsed_ms=elapsed)
    return outcome.value, call_id


# Provider roles ----------------------------------------------------------------
class Tracker(Protocol):
    model_id: str

    def select_instances(self, ctx: RunContext, *, image_png: bytes, width: int, height: int,
                         phrase: str, proxy_frame_index: int) -> list[SelectionCandidate]: ...

    def track(self, ctx: RunContext, *, video: ProxyVideo, prompt: ConceptPrompt | BoxPrompt,
              role: Literal["target", "person"]) -> TrackingResult: ...


class Assessor(Protocol):
    model_id: str

    def assess(self, ctx: RunContext, *, candidate: Candidate, frames: list[EvidenceItem],
               frame_bytes: dict[str, bytes], cue_table: dict[str, Any], purpose: str) -> AssessmentResult: ...


@dataclass
class FunctionCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text; validated by the controller before dispatch


@dataclass
class ModelTurn:
    response_id: str | None
    function_calls: list[FunctionCall]
    text: str | None  # prose is never published; it triggers a structured repair
    status: str
    output_items: list[dict[str, Any]] = field(default_factory=list)  # replayed verbatim as history next turn


class InvestigatorModel(Protocol):
    model_id: str

    def plan(self, ctx: RunContext, *, question: str, object_id: str, clip_duration_ms: int) -> Plan: ...

    def turn(self, ctx: RunContext, *, instructions: str, input_items: list[dict[str, Any]],
             previous_response_id: str | None, tools: list[dict[str, Any]]) -> ModelTurn: ...


class Reviewer(Protocol):
    model_id: str

    def review(self, ctx: RunContext, *, question: str, claims: list[dict[str, Any]],
               evidence: list[EvidenceItem], frame_bytes: dict[str, bytes], cues: list[dict[str, Any]],
               mode: Literal["image_secondary", "text_secondary"]) -> AuditResult: ...


@dataclass
class ProviderSet:
    """The immutable provider bundle for one run mode."""

    run_mode: RunMode
    tracker: Tracker
    assessor: Assessor
    investigator: InvestigatorModel
    reviewer: Reviewer | None
    review_mode: Literal["image_secondary", "text_secondary", "not_run"]


__all__ = [
    "Assessor", "BudgetExceeded", "CallOutcome", "CancelToken", "FunctionCall", "InvestigatorModel", "Ledger",
    "ModelTurn", "ProviderError", "ProviderSet", "ProviderUnavailable", "RecordingMiss", "Reviewer", "RunContext",
    "Tracker", "metered_call",
]
