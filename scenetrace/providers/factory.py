"""Build the immutable provider bundle for a run mode. Fixture never touches the network;
recorded_real replays a recording and never runs a model; live never substitutes either."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..db import Database
from .base import ProviderSet, ProviderUnavailable
from .fixture_reasoning import FixtureAssessor, FixtureInvestigatorModel
from .fixture_tracking import FixtureTracker
from .meta_muse import MetaAssessor, MetaInvestigatorModel, MetaReviewer
from .meta_sam import MetaSamTracker
from .recording import Recording, RecordingTransport, ReplayTransport
from .transport import MetaTransport


def reviewer_capability(settings: Settings) -> str:
    path = settings.root / "reports" / "provider-capabilities.json"
    try:
        checks = json.loads(path.read_text(encoding="utf-8"))["checks"]
    except (OSError, ValueError, KeyError):
        return "not_tested"
    return str(checks.get("muse_wonder_image_structured", {}).get("status", "not_tested"))


def review_mode(settings: Settings) -> str:
    if not settings.enable_secondary_review:
        return "not_run"
    return "image_secondary" if reviewer_capability(settings) == "passed" else "not_run"


def build_providers(settings: Settings, db: Database, run_mode: str, *, recording_dir: Path) -> ProviderSet:
    if run_mode == "fixture":
        return ProviderSet(run_mode="fixture", tracker=FixtureTracker(), assessor=FixtureAssessor(),
                           investigator=FixtureInvestigatorModel(), reviewer=None, review_mode="not_run")
    if run_mode == "recorded_real":
        transport = ReplayTransport(Recording(recording_dir))
    elif run_mode == "live":
        if not settings.model_api_key:
            raise ProviderUnavailable("NO_API_KEY")
        transport = RecordingTransport(MetaTransport(settings.provider_base_url, settings.model_api_key),
                                       Recording(recording_dir))
    else:
        raise ValueError(f"unknown run mode {run_mode}")
    mode = review_mode(settings)
    return ProviderSet(run_mode=run_mode, tracker=MetaSamTracker(transport=transport, settings=settings, db=db),  # type: ignore[arg-type]
                       assessor=MetaAssessor(transport=transport, settings=settings),
                       investigator=MetaInvestigatorModel(transport=transport, settings=settings),
                       reviewer=MetaReviewer(transport=transport, settings=settings) if mode != "not_run" else None,
                       review_mode=mode)  # type: ignore[arg-type]
