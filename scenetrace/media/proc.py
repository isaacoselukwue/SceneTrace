"""Run ffmpeg and ffprobe safely: argument arrays, no shell, minimal environment,
finite deadlines and cancellation that kills the child process."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from ..errors import AppError, Cancelled
from ..providers.base import CancelToken


class MediaToolError(AppError):
    def __init__(self, code: str, diagnostic: str | None = None) -> None:
        super().__init__(code, "The media file could not be processed.", status_code=422, diagnostic=diagnostic)


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaToolError("MEDIA_TOOL_MISSING", diagnostic=name)
    return path


def child_env() -> dict[str, str]:
    """Only PATH reaches media children; no keys, cookies or proxies."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}


@dataclass(frozen=True)
class ProcResult:
    returncode: int
    stdout: bytes
    stderr: str


def run(cmd: list[str], *, timeout_s: float, cancel: CancelToken | None = None) -> ProcResult:
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=child_env(),
                                close_fds=True, start_new_session=True)
        deadline = time.monotonic() + timeout_s
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.cancelled:
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise MediaToolError("MEDIA_TIMEOUT", diagnostic=os.path.basename(cmd[0]))
                time.sleep(0.05)
        except BaseException:
            kill(proc)
            raise
        out.seek(0)
        err.seek(0)
        return ProcResult(proc.returncode, out.read(), err.read().decode("utf-8", "replace")[-2000:])


def kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def version(name: str) -> str:
    result = run([tool(name), "-hide_banner", "-version"], timeout_s=10)
    first = result.stdout.decode("utf-8", "replace").splitlines()
    return first[0] if first else "unknown"
