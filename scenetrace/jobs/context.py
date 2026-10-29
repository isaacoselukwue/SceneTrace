"""Process-wide services shared by the API and the worker."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..budget import DurableLedger
from ..config import Settings
from ..db import Database
from ..storage import Storage
from ..store import AnalysisStore
from .events import JobEvents


def sample_registry(root: Path) -> dict[str, str]:
    """SHA-256 of the bundled synthetic clips, so an upload of one is labelled synthetic, not real footage."""
    out: dict[str, str] = {}
    for label in sorted((root / "samples" / "synthetic").glob("*.label.json")):
        video = label.with_name(label.name.replace(".label.json", ".mp4"))
        if video.is_file():
            out[hashlib.sha256(video.read_bytes()).hexdigest()] = json.loads(label.read_text())["clip_id"]
    return out


@dataclass
class Services:
    settings: Settings
    db: Database
    storage: Storage
    store: AnalysisStore
    ledger: DurableLedger
    events: JobEvents
    samples: dict[str, str]

    @classmethod
    def create(cls, settings: Settings) -> Services:
        db = Database(settings.data_dir / "db" / "scenetrace.sqlite3")
        storage = Storage(settings.data_dir, db)
        return cls(settings=settings, db=db, storage=storage, store=AnalysisStore(db, storage),
                   ledger=DurableLedger(db, settings), events=JobEvents(db), samples=sample_registry(settings.root))
