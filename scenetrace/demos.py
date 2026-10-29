"""Approved recorded demonstrations: packaged from completed analyses and imported read-only."""
from __future__ import annotations

from pathlib import Path

from .jobs.context import Services


def publish_demo(svc: Services, analysis_id: str, *, title: str, description: str, approval_note: str, out_dir: Path) -> Path:
    raise NotImplementedError("publishing is implemented with the recorded demo package")


def import_demos(svc: Services, source: Path) -> list[str]:
    raise NotImplementedError("importing is implemented with the recorded demo package")
