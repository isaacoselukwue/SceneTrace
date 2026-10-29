from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    call_id: str
    amount_microusd: int


class BudgetLedger:
    """In-memory reference algorithm; the deployed worker must persist it atomically."""

    def __init__(self, cap_microusd: int, max_provider_calls: int) -> None:
        if cap_microusd < 0 or max_provider_calls < 0:
            raise ValueError("Budget limits cannot be negative")
        self.cap_microusd = cap_microusd
        self.max_provider_calls = max_provider_calls
        self.charged_microusd = 0
        self.provider_calls = 0
        self.uncertain_calls: set[str] = set()
        self._pending: dict[str, Reservation] = {}
        self._seen: set[str] = set()
        self._lock = RLock()

    @property
    def pending_microusd(self) -> int:
        with self._lock:
            return sum(r.amount_microusd for r in self._pending.values())

    def reserve(self, call_id: str, upper_bound_microusd: int) -> Reservation:
        with self._lock:
            if not call_id or call_id in self._seen:
                raise ValueError("Call identifiers must be non-empty and unique")
            if upper_bound_microusd < 0:
                raise ValueError("Reservation cannot be negative")
            if self.provider_calls >= self.max_provider_calls:
                raise BudgetExceeded("PROVIDER_CALL_LIMIT")
            if self.charged_microusd + self.pending_microusd + upper_bound_microusd > self.cap_microusd:
                raise BudgetExceeded("COST_LIMIT")
            reservation = Reservation(call_id, upper_bound_microusd)
            self._pending[call_id] = reservation
            self._seen.add(call_id)
            self.provider_calls += 1
            return reservation

    def settle(self, call_id: str, actual_microusd: int | None) -> None:
        with self._lock:
            if actual_microusd is not None and actual_microusd < 0:
                raise ValueError("Actual cost cannot be negative")
            reservation = self._pending.pop(call_id)
            if actual_microusd is None:
                self.charged_microusd += reservation.amount_microusd
                self.uncertain_calls.add(call_id)
            else:
                self.charged_microusd += actual_microusd
            if self.charged_microusd + self.pending_microusd > self.cap_microusd:
                raise BudgetExceeded("BUDGET_OVERRUN_REQUIRES_RECONCILIATION")

    def release_unsent(self, call_id: str) -> None:
        with self._lock:
            self._pending.pop(call_id)


def sam_video_microusd(frame_count: int, concept_requests: int = 1) -> int:
    if frame_count < 0 or concept_requests < 1:
        raise ValueError("Invalid segmentation quantity")
    return max(50, frame_count) * concept_requests * 200
