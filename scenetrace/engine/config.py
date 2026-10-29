"""Engine thresholds. Distances are in units of the target scale (median target
box diagonal), stored as integer basis points so the configuration can be hashed."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EngineConfig:
    stationary_window_samples: int = 8
    stationary_displacement_bp: int = 2_000   # <= 0.20 target scale from the local median
    movement_trigger_bp: int = 7_500          # >= 0.75 target scale
    movement_interval_ms: int = 500
    interaction_distance_bp: int = 5_000      # box edge proximity, not proof of contact
    proximity_persistence_samples: int = 3
    association_margin_bp: int = 2_500
    max_gap_samples: int = 3
    merge_window_ms: int = 700
    max_candidates: int = 8
    evidence_half_window_ms: int = 1_500
    min_target_scale_px: int = 8
    release_stationary_samples: int = 20      # stationary this long counts as stable support even with someone near

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


DEFAULT_ENGINE_CONFIG = EngineConfig()
