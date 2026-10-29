"""Foundation tests: SSE decoding, durable ledger, configuration and storage safety."""
from __future__ import annotations

import json
import threading

import pytest

from scenetrace.budget import DurableLedger
from scenetrace.config import Settings
from scenetrace.errors import BudgetExceeded
from scenetrace.providers.base import CallOutcome, ProviderError, RunContext, metered_call
from scenetrace.providers.sse import SSEDecoder, decode_json_event
from scenetrace.storage import Storage


# SSE ---------------------------------------------------------------------------
def _events(chunks: list[bytes]) -> list[dict]:
    decoder = SSEDecoder()
    out = []
    for chunk in chunks:
        out.extend(decoder.feed(chunk))
    out.extend(decoder.close())
    return [e for e in (decode_json_event(x) for x in out) if e is not None]


def test_sse_split_inside_json_and_utf8() -> None:
    payload = json.dumps({"type": "response.output_text.delta", "delta": "café → <0f>0<|box|>"}).encode()
    raw = b"event: response.output_text.delta\ndata: " + payload + b"\n\n"
    # Split at every byte offset, including inside the multi-byte characters.
    for cut in range(1, len(raw)):
        events = _events([raw[:cut], raw[cut:]])
        assert events == [json.loads(payload)]


def test_sse_multiple_events_comments_crlf_and_keepalive() -> None:
    raw = (b": heartbeat\r\n\r\n"
           b"data: {\"type\":\"response.created\",\"response\":{\"id\":\"r1\"}}\r\n\r\n"
           b"data: {\"type\":\"keepalive\",\"sequence_number\":1}\r\n\r\n"
           b"data: {\"type\":\"response.completed\",\"response\":{\"id\":\"r1\"}}\r\n\r\n"
           b"data: [DONE]\r\n\r\n")
    events = _events([raw])
    assert [e["type"] for e in events] == ["response.created", "response.completed"]


def test_sse_cr_split_across_chunks() -> None:
    raw = b"data: {\"type\":\"a\"}\r\n\r\n"
    index = raw.index(b"\r")
    assert [e["type"] for e in _events([raw[: index + 1], raw[index + 1:]])] == ["a"]


def test_sse_trailing_event_without_blank_line() -> None:
    assert [e["type"] for e in _events([b"data: {\"type\":\"response.output_text.delta\"}"])] == ["response.output_text.delta"]


def test_sse_malformed_json_is_an_error() -> None:
    from scenetrace.providers.sse import SSEDecodeError
    decoder = SSEDecoder()
    with pytest.raises(SSEDecodeError):
        for event in decoder.feed(b"data: {not json}\n\n"):
            decode_json_event(event)


# Ledger --------------------------------------------------------------------------
def _ctx(ledger: DurableLedger, scope: str = "cold", **kw) -> RunContext:
    return RunContext(owner_id="owner_local", scope=scope, run_mode="live", ledger=ledger,
                      analysis_id=kw.get("analysis_id", "ana_" + "a" * 32), investigation_id=kw.get("investigation_id"))


def test_ledger_reserve_settle_and_caps(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    ctx = _ctx(ledger)
    cap = settings.defaults["analysis_cap_microusd"]
    call = ledger.reserve(ctx, operation="sam_video", model_id="sam-3.1", upper_bound_microusd=cap - 10, attempt=1)
    with pytest.raises(BudgetExceeded) as exc:
        ledger.reserve(ctx, operation="sam_video", model_id="sam-3.1", upper_bound_microusd=11, attempt=1)
    assert exc.value.code == "COST_LIMIT_RUN"
    ledger.mark_dispatched(call)
    ledger.settle(call, actual_microusd=40_000, usage={"video_frames_processed": 200}, request_id="r", response_id="x",
                  http_status=200, elapsed_ms=5)
    state = ledger.scope_state(f"analysis:{ctx.analysis_id}")
    assert state["reserved_microusd"] == 0 and state["charged_microusd"] == 40_000


def test_ledger_unknown_retains_reservation(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    ctx = _ctx(ledger)

    def boom() -> CallOutcome:
        raise ProviderError("STREAM_DISCONNECTED", ambiguous=True)

    with pytest.raises(ProviderError):
        metered_call(ctx, operation="sam_video", model_id="sam-3.1", upper_bound_microusd=40_000, dispatch=boom)
    state = ledger.scope_state(f"analysis:{ctx.analysis_id}")
    assert state["charged_microusd"] == 40_000  # retained, not refunded
    row = db.one("SELECT cost_status, state FROM provider_calls")
    assert (row["cost_status"], row["state"]) == ("unknown", "unknown")


def test_ledger_rejected_request_is_not_charged(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    ctx = _ctx(ledger)

    def bad_request() -> CallOutcome:
        raise ProviderError("invalid_prompt", http_status=400, ambiguous=False)

    with pytest.raises(ProviderError):
        metered_call(ctx, operation="sam_image", model_id="sam-3.1", upper_bound_microusd=2_500, dispatch=bad_request)
    assert ledger.scope_state(f"analysis:{ctx.analysis_id}")["charged_microusd"] == 0


def test_ledger_attempt_cap_counts_retries(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    ctx = _ctx(ledger, scope="warm", investigation_id="inv_" + "b" * 32)
    limit = settings.defaults["max_provider_attempts_warm"]
    for i in range(limit):
        call = ledger.reserve(ctx, operation="investigate", model_id="muse-spark-1.3", upper_bound_microusd=1, attempt=i + 1)
        ledger.release_unsent(call)
    with pytest.raises(BudgetExceeded) as exc:
        ledger.reserve(ctx, operation="investigate", model_id="muse-spark-1.3", upper_bound_microusd=1, attempt=limit + 1)
    assert exc.value.code == "PROVIDER_CALL_LIMIT"


def test_ledger_parallel_reservations_cannot_overspend(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    ctx = _ctx(ledger)
    cap = settings.defaults["analysis_cap_microusd"]
    amount = cap // 3 + 1  # only two of these fit
    results: list[str] = []
    errors: list[str] = []

    def worker() -> None:
        try:
            results.append(ledger.reserve(ctx, operation="sam_video", model_id="sam-3.1", upper_bound_microusd=amount, attempt=1))
        except BudgetExceeded as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 2 and len(errors) == 6
    assert ledger.scope_state(f"analysis:{ctx.analysis_id}")["reserved_microusd"] == 2 * amount


def test_paid_call_requires_positive_bound(db, settings: Settings) -> None:
    ledger = DurableLedger(db, settings)
    with pytest.raises(ValueError):
        ledger.reserve(_ctx(ledger), operation="assess", model_id="muse-spark-1.3", upper_bound_microusd=0, attempt=1)


# Rates and configuration -------------------------------------------------------------
def test_rates_cached_tokens_not_charged_twice(settings: Settings) -> None:
    rates = settings.rates
    assert rates.sam_video_cost(10) == 50 * 200
    assert rates.sam_video_cost(200) == 40_000
    full = rates.token_cost("muse-spark-1.3", 1_000_000, 0, 0)
    cached = rates.token_cost("muse-spark-1.3", 1_000_000, 1_000_000, 0)
    assert (full, cached) == (1_250_000, 150_000)


def test_settings_repr_hides_secrets(settings: Settings) -> None:
    loaded = Settings.load(settings.root, env={"MODEL_API_KEY": "secret-value-123", "SCENETRACE_DATA_DIR": str(settings.data_dir)})
    assert "secret-value-123" not in repr(loaded)


def test_storage_refuses_traversal_and_symlinks(db, settings: Settings, tmp_path) -> None:
    storage = Storage(settings.data_dir, db)
    for bad in ("../x", "/etc/passwd", "private/../../x", "a//b"):
        with pytest.raises(ValueError):
            storage.contained(bad)
    target = settings.data_dir / "private" / "link"
    target.symlink_to(tmp_path)
    with pytest.raises(ValueError):
        storage.contained("private/link/file")
