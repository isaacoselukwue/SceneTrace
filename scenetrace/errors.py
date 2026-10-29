"""Application errors with stable public codes. Provider internals stay in private diagnostics."""
from __future__ import annotations


class AppError(Exception):
    """An error that maps to the public JSON error envelope."""

    status_code = 400
    retryable = False

    def __init__(self, code: str, message: str, *, status_code: int | None = None,
                 retryable: bool | None = None, diagnostic: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.diagnostic = diagnostic  # sanitised, private


def not_found(what: str = "resource") -> AppError:
    # Identical response for absent and other-owner resources.
    return AppError("NOT_FOUND", f"The requested {what} was not found.", status_code=404)


class Cancelled(Exception):
    """Raised when a cancellation token fires between steps."""


class DeadlineExceeded(Exception):
    """Raised when a job deadline is reached; callers record partial results."""


class BudgetExceeded(Exception):
    """A reservation would exceed a cost or attempt cap. No provider call was made."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
