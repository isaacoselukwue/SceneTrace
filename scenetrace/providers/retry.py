"""At most one retry for an eligible transient failure, as a new metered attempt."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .base import ProviderError, ProviderUnavailable, RecordingMiss, RunContext

T = TypeVar("T")
MAX_WAIT_S = 30.0


def with_one_retry(ctx: RunContext, attempt: Callable[[int], T]) -> T:
    """Run attempt(1); on a retryable, non-ambiguous failure wait (honouring Retry-After) and run attempt(2).

    Ambiguous failures (work may already be billed), 4xx rejections, missing model
    access and replay misses are never retried.
    """
    try:
        return attempt(1)
    except (ProviderUnavailable, RecordingMiss):
        raise
    except ProviderError as exc:
        if not exc.retryable or exc.ambiguous or ctx.run_mode != "live":
            raise
        wait = exc.retry_after_s if exc.retry_after_s is not None else 1.0
        remaining = ctx.remaining_s()
        if wait > MAX_WAIT_S or (remaining is not None and wait + 5 > remaining):
            raise
        if ctx.cancel._event.wait(wait):  # returns early when cancelled
            ctx.cancel.check()
        return attempt(2)
