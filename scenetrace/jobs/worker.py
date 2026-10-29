"""One bounded inference worker. Jobs are claimed in a SQLite transaction; a restart marks
running jobs interrupted instead of silently resending billed work."""
from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from ..errors import AppError, BudgetExceeded, Cancelled, DeadlineExceeded
from ..ids import utc_now
from ..logs import get
from ..providers.base import CancelToken, ProviderError, ProviderUnavailable, RecordingMiss
from .context import Services

log = get("worker")
ACTIVE = ("queued", "running", "cancel_requested")


class Worker:
    def __init__(self, svc: Services, handlers: dict[str, Callable] | None = None) -> None:
        from . import pipeline
        self.svc = svc
        self.handlers = handlers or {"normalise": pipeline.run_normalise, "analysis": pipeline.run_analysis,
                                     "question": pipeline.run_question}
        self.tokens: dict[str, CancelToken] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.alive = False

    def start(self) -> None:
        self.reconcile()
        self._thread = threading.Thread(target=self._loop, name="scenetrace-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for token in self.tokens.values():
            token.cancel()
        if self._thread:
            self._thread.join(timeout=10)

    def reconcile(self) -> None:
        rows = self.svc.db.query("SELECT id, kind, analysis_id, investigation_id FROM jobs WHERE state IN ('running', 'cancel_requested')")
        for row in rows:
            self._finish(dict(row), "interrupted", "job.failed", "INTERRUPTED",
                         "The server restarted while this job was running. Partial results were kept; start a new run to retry.")

    def cancel(self, job_id: str) -> None:
        token = self.tokens.get(job_id)
        if token is not None:
            token.cancel()

    def _claim(self) -> dict | None:
        with self.svc.db.write() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE jobs SET state = 'running', started_at = ? WHERE id = ? AND state = 'queued'", (utc_now(), row["id"]))
            return dict(row)

    def _loop(self) -> None:
        self.alive = True
        while not self._stop.is_set():
            try:
                job = self._claim()
            except Exception:  # noqa: BLE001 - keep the worker alive on transient database errors
                log.exception("job claim failed")
                job = None
            if job is None:
                self.svc.events.wait(1.0)
                continue
            self.run_job(job)
        self.alive = False

    def run_job(self, job: dict) -> None:
        token = self.tokens.setdefault(job["id"], CancelToken())
        mode = job.get("run_mode")
        self.svc.events.append(job["id"], "stage.started", {"stage": "validating", "summary": "Job started."}, mode=mode)
        try:
            state = self.handlers[job["kind"]](self.svc, job, token)
            event = {"completed": "job.completed", "partial": "job.partial", "needs_selection": "job.needs_selection"}[state]
            summary = {"completed": "Finished.", "partial": "Finished with partial results; see the listed reasons.",
                       "needs_selection": "Waiting for the object to be selected."}[state]
            self._finish(job, state, event, None, summary)
        except Cancelled:
            self._finish(job, "cancelled", "job.cancelled", "CANCELLED", "Cancelled. No new provider calls were scheduled.")
        except DeadlineExceeded:
            self._finish(job, "partial", "job.partial", "DEADLINE_REACHED", "The time limit was reached; partial results were kept.")
        except BudgetExceeded as exc:
            self._finish(job, "partial", "job.partial", "BUDGET_STOPPED", f"Stopped at the configured cost limit ({exc.code}).")
        except RecordingMiss:
            self._finish(job, "failed", "job.failed", "RECORDING_MISS", "This recorded run has no recording for that request."
                         " Live inference is not used in recorded mode.")
        except ProviderUnavailable as exc:
            self._finish(job, "failed", "job.failed", "PROVIDER_UNAVAILABLE", f"The model service is unavailable ({exc.code}).")
        except ProviderError as exc:
            self._finish(job, "failed", "job.failed", "PROVIDER_ERROR", f"The model service returned an error ({exc.code}).")
        except AppError as exc:
            self._finish(job, "failed", "job.failed", exc.code, exc.message)
        except Exception:  # noqa: BLE001 - sanitised failure; details stay in the private log
            log.error("job failed: %s", traceback.format_exc(limit=8), extra={"job_id": job["id"]})
            self._finish(job, "failed", "job.failed", "INTERNAL_ERROR", "The job failed unexpectedly. Partial results were kept.")
        finally:
            self.tokens.pop(job["id"], None)
            from .cleanup import cleanup_remote_files
            if job["kind"] == "analysis":
                cleanup_remote_files(self.svc)

    def _finish(self, job: dict, state: str, event: str, code: str | None, summary: str) -> None:
        db = self.svc.db
        with db.write() as conn:
            conn.execute("UPDATE jobs SET state = ?, finished_at = ?, error_code = ?, error_message = ? WHERE id = ?",
                         (state, utc_now(), code, summary if code else None, job["id"]))
            terminal = {"interrupted": "interrupted", "cancelled": "cancelled", "failed": "failed", "partial": "partial",
                        "completed": "completed", "needs_selection": "needs_selection"}[state]
            if job.get("analysis_id") and job["kind"] == "analysis":
                # The pipeline sets completed/partial/needs_selection itself; the worker only closes runs it left open.
                conn.execute("UPDATE analyses SET status = ? WHERE id = ? AND status IN ('queued', 'running')",
                             (terminal, job["analysis_id"]))
            if job.get("investigation_id"):
                conn.execute("UPDATE investigations SET status = ? WHERE id = ? AND status IN ('queued', 'running')",
                             ({"needs_selection": "failed"}.get(terminal, terminal), job["investigation_id"]))
            if job["kind"] == "normalise" and state in ("failed", "interrupted", "cancelled"):
                conn.execute("UPDATE clips SET status = CASE WHEN status = 'rejected' THEN status ELSE 'failed' END WHERE id = ?",
                             (job.get("clip_id"),))
        self.svc.events.append(job["id"], event, {"summary": summary, "error_code": code}, mode=job.get("run_mode"))
