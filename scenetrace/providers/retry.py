from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .base import ProviderError, ProviderUnavailable, RecordingMiss, RunContext

T = TypeVar("T")
MAX_WAIT_S = 30.0


def with_one_retry(ctx: RunContext, attempt: Callable[[int], T]) -> T:
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
        if ctx.cancel._event.wait(wait):
            ctx.cancel.check()
        return attempt(2)
