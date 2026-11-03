from __future__ import annotations


class AppError(Exception):
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
        self.diagnostic = diagnostic


def not_found(what: str = "resource") -> AppError:
    return AppError("NOT_FOUND", f"The requested {what} was not found.", status_code=404)


class Cancelled(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


class BudgetExceeded(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
