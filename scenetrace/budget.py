from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import Settings
from .db import Database, dumps, loads
from .errors import BudgetExceeded
from .ids import new_id, utc_day, utc_now
from .logs import get

if TYPE_CHECKING:
    from .providers.base import RunContext

log = get("budget")


class DurableLedger:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def _scopes(self, ctx: RunContext) -> list[tuple[str, int, int | None]]:
        d = self.settings.defaults
        if ctx.scope == "cold":
            run = (f"analysis:{ctx.analysis_id}", d["analysis_cap_microusd"], d["max_provider_attempts_cold"])
        elif ctx.scope == "warm":
            run = (f"question:{ctx.investigation_id}", d["question_cap_microusd"], d["max_provider_attempts_warm"])
        elif ctx.scope == "eval":
            run = (f"eval:{ctx.job_id or ctx.analysis_id}", d.get("eval_cap_microusd", 3_000_000), None)
        elif ctx.scope == "cleanup":
            run = ("cleanup", 0, None)
        else:
            run = ("spike", 500_000, None)
        return [run, (f"day:{utc_day()}", d["daily_cap_microusd"], None), ("total", d["total_cap_microusd"], None)]

    def reserve(self, ctx: RunContext, *, operation: str, model_id: str, upper_bound_microusd: int,
                attempt: int, paid: bool = True) -> str:
        if upper_bound_microusd < 0:
            raise ValueError("Reservation cannot be negative")
        if paid and upper_bound_microusd == 0:
            raise ValueError("A paid call needs a positive upper bound; unknown cost is blocked, not zero")
        scopes = self._scopes(ctx)
        call_id = new_id("call")
        now = utc_now()
        with self.db.write() as conn:
            for key, cap, max_attempts in scopes:
                conn.execute("INSERT OR IGNORE INTO budget_scopes(scope_key, cap_microusd, max_attempts, updated_at) VALUES (?, ?, ?, ?)",
                             (key, cap, max_attempts, now))
            if paid:
                for index, (key, _cap, _max) in enumerate(scopes):
                    row = conn.execute("SELECT * FROM budget_scopes WHERE scope_key = ?", (key,)).fetchone()
                    if row["max_attempts"] is not None and row["attempts"] >= row["max_attempts"]:
                        raise BudgetExceeded("PROVIDER_CALL_LIMIT")
                    if row["charged_microusd"] + row["reserved_microusd"] + upper_bound_microusd > row["cap_microusd"]:
                        raise BudgetExceeded(("COST_LIMIT_RUN", "COST_LIMIT_DAY", "COST_LIMIT_TOTAL")[index])
                for key, _cap, _max in scopes:
                    conn.execute("UPDATE budget_scopes SET reserved_microusd = reserved_microusd + ?, attempts = attempts + 1,"
                                 " updated_at = ? WHERE scope_key = ?", (upper_bound_microusd, now, key))
            conn.execute(
                "INSERT INTO provider_calls(id, owner_id, job_id, analysis_id, investigation_id, scope, operation, model_id,"
                " attempt, run_mode, state, reserved_microusd, cost_status, rate_card_revision, scope_keys_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?)",
                (call_id, ctx.owner_id, ctx.job_id, ctx.analysis_id, ctx.investigation_id, ctx.scope, operation, model_id,
                 attempt, ctx.run_mode, upper_bound_microusd if paid else 0, "estimated" if paid else "free",
                 self.settings.rates.revision, dumps([s[0] for s in scopes] if paid else []), now))
        ctx.emit("budget.updated", {"operation": operation, "model_id": model_id, "reserved_microusd": upper_bound_microusd if paid else 0,
                                    "provider_call_id": call_id})
        return call_id

    def mark_dispatched(self, call_id: str) -> None:
        with self.db.write() as conn:
            conn.execute("UPDATE provider_calls SET state = 'dispatched', dispatched_at = ? WHERE id = ? AND state = 'reserved'",
                         (utc_now(), call_id))

    def settle(self, call_id: str, *, actual_microusd: int | None, usage: dict | None, request_id: str | None,
               response_id: str | None, http_status: int | None, elapsed_ms: int | None,
               error_code: str | None = None) -> None:
        if actual_microusd is not None and actual_microusd < 0:
            raise ValueError("Actual cost cannot be negative")
        with self.db.write() as conn:
            call = conn.execute("SELECT * FROM provider_calls WHERE id = ?", (call_id,)).fetchone()
            if call is None or call["state"] not in ("reserved", "dispatched"):
                raise ValueError("call is not awaiting settlement")
            reserved = int(call["reserved_microusd"])
            paid = call["cost_status"] != "free"
            if not paid:
                charged, status, state = 0, "free", "settled"
            elif actual_microusd is None:
                charged, status, state = reserved, "unknown", "unknown"
            else:
                charged = actual_microusd
                status = "provider_usage_based" if usage is not None else "estimated"
                state = "settled"
            for key in loads(call["scope_keys_json"], []):
                conn.execute("UPDATE budget_scopes SET reserved_microusd = MAX(0, reserved_microusd - ?),"
                             " charged_microusd = charged_microusd + ?, updated_at = ? WHERE scope_key = ?",
                             (reserved, charged, utc_now(), key))
            conn.execute(
                "UPDATE provider_calls SET state = ?, actual_microusd = ?, cost_status = ?, usage_json = ?, request_id = ?,"
                " response_id = ?, http_status = ?, error_code = ?, finished_at = ?, elapsed_ms = ? WHERE id = ?",
                (state, None if actual_microusd is None and paid else charged, status, dumps(usage) if usage is not None else None,
                 request_id, response_id, http_status, error_code, utc_now(), elapsed_ms, call_id))
            if paid and actual_microusd is not None and actual_microusd > reserved:
                log.warning("actual cost exceeded reservation", extra={"event_code": "BUDGET_OVERRUN_REQUIRES_RECONCILIATION"})

    def release_unsent(self, call_id: str) -> None:
        with self.db.write() as conn:
            call = conn.execute("SELECT * FROM provider_calls WHERE id = ?", (call_id,)).fetchone()
            if call is None or call["state"] != "reserved":
                raise ValueError("only an undispatched reservation can be released")
            for key in loads(call["scope_keys_json"], []):
                conn.execute("UPDATE budget_scopes SET reserved_microusd = MAX(0, reserved_microusd - ?), updated_at = ?"
                             " WHERE scope_key = ?", (call["reserved_microusd"], utc_now(), key))
            conn.execute("UPDATE provider_calls SET state = 'released', cost_status = 'released', actual_microusd = 0,"
                         " finished_at = ? WHERE id = ?", (utc_now(), call_id))

    def reconcile_unknown(self, call_id: str, actual_microusd: int, usage: dict | None) -> None:
        with self.db.write() as conn:
            call = conn.execute("SELECT * FROM provider_calls WHERE id = ?", (call_id,)).fetchone()
            if call is None or call["state"] != "unknown":
                raise ValueError("call is not in the unknown state")
            delta = actual_microusd - int(call["reserved_microusd"])
            for key in loads(call["scope_keys_json"], []):
                conn.execute("UPDATE budget_scopes SET charged_microusd = MAX(0, charged_microusd + ?), updated_at = ?"
                             " WHERE scope_key = ?", (delta, utc_now(), key))
            conn.execute("UPDATE provider_calls SET state = 'settled', actual_microusd = ?, cost_status = 'provider_usage_based',"
                         " usage_json = ? WHERE id = ?", (actual_microusd, dumps(usage) if usage else None, call_id))

    def record_replay(self, ctx: RunContext, *, operation: str, model_id: str, original_call: dict[str, Any]) -> str:
        call_id = new_id("call")
        usage = {"replayed_from": original_call.get("provider_call_id"),
                 "original_microusd": original_call.get("actual_microusd"),
                 "original_cost_status": original_call.get("cost_status"),
                 "original_usage": original_call.get("usage")}
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO provider_calls(id, owner_id, job_id, analysis_id, investigation_id, scope, operation, model_id,"
                " attempt, run_mode, state, reserved_microusd, actual_microusd, cost_status, usage_json, rate_card_revision,"
                " scope_keys_json, created_at, finished_at, elapsed_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'replayed', 0, 0, 'replayed', ?, ?, '[]', ?, ?, 0)",
                (call_id, ctx.owner_id, ctx.job_id, ctx.analysis_id, ctx.investigation_id, ctx.scope, operation, model_id,
                 ctx.run_mode, dumps(usage), self.settings.rates.revision, utc_now(), utc_now()))
        return call_id

    def scope_state(self, scope_key: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM budget_scopes WHERE scope_key = ?", (scope_key,))
        return dict(row) if row is not None else None

    def calls_for(self, *, analysis_id: str | None = None, investigation_id: str | None = None) -> list[dict[str, Any]]:
        if investigation_id is not None:
            rows = self.db.query("SELECT * FROM provider_calls WHERE investigation_id = ? ORDER BY created_at", (investigation_id,))
        else:
            rows = self.db.query("SELECT * FROM provider_calls WHERE analysis_id = ? AND investigation_id IS NULL"
                                 " ORDER BY created_at", (analysis_id,))
        return [dict(r) for r in rows]
