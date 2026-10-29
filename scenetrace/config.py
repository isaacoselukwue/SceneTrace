"""Settings loaded from the environment plus the checked-in configuration files.

Secrets are read on the server only and are never included in repr, logs or API output.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
RunMode = Literal["fixture", "recorded_real", "live"]


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Rates:
    """Rate card from config/rates.json. Money is integer micro-US dollars."""

    revision: str
    sam_image_microusd: int
    sam_video_frame_microusd: int
    sam_min_video_frames: int
    text_models: dict[str, dict[str, int]]

    @classmethod
    def load(cls, path: Path) -> Rates:
        raw = json.loads(path.read_text(encoding="utf-8"))
        sam = raw["sam-3.1"]
        return cls(
            revision=f"{raw['as_of']}:{raw['status']}",
            sam_image_microusd=sam["image_microusd"],
            sam_video_frame_microusd=sam["video_frame_microusd"],
            sam_min_video_frames=sam["minimum_video_frames_per_request"],
            text_models=raw["text_models"],
        )

    def sam_video_cost(self, frames: int) -> int:
        if frames < 0:
            raise ValueError("frame count cannot be negative")
        return max(self.sam_min_video_frames, frames) * self.sam_video_frame_microusd

    def sam_image_cost(self, images: int = 1) -> int:
        return images * self.sam_image_microusd

    def token_cost(self, model_id: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> int:
        """Usage-based cost. Cached tokens are a subset of input tokens and are not charged twice."""
        rate = self.text_models.get(model_id)
        if rate is None:
            raise KeyError(f"no rate for {model_id}")
        cached = max(0, min(cached_tokens, input_tokens))
        uncached = input_tokens - cached
        total = (uncached * rate["input_microusd_per_million"]
                 + cached * rate["cached_input_microusd_per_million"]
                 + output_tokens * rate["output_microusd_per_million"])
        return -(-total // 1_000_000)  # ceiling division

    def token_upper_bound(self, model_id: str, counted_input_tokens: int, max_output_tokens: int) -> int:
        """Worst case before dispatch: every counted input token uncached plus the full output budget."""
        return self.token_cost(model_id, counted_input_tokens, 0, max_output_tokens)


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    default_mode: RunMode
    model_api_key: str | None = field(repr=False)
    demo_access_code: str | None = field(repr=False)
    enable_secondary_review: bool
    enable_live_uploads: bool
    run_live_tests: bool
    public_https: bool
    bind_host: str
    defaults: dict[str, Any]
    rates: Rates
    demo_read_only: bool = False

    @property
    def provider_base_url(self) -> str:
        return str(self.defaults["provider_base_url"])

    @property
    def models(self) -> dict[str, str]:
        return dict(self.defaults["models"])

    @property
    def live_available(self) -> bool:
        return bool(self.model_api_key)

    def limit(self, name: str) -> int:
        return int(self.defaults[name])

    @classmethod
    def load(cls, root: Path = ROOT, env: dict[str, str] | None = None) -> Settings:
        merged = _read_dotenv(root / ".env")
        merged.update({k: v for k, v in os.environ.items() if k.startswith(("SCENETRACE_", "MODEL_API_KEY"))})
        if env is not None:
            merged.update(env)
        defaults = json.loads((root / "config" / "defaults.json").read_text(encoding="utf-8"))
        data_dir = Path(merged.get("SCENETRACE_DATA_DIR") or defaults.get("data_dir", ".local/data"))
        if not data_dir.is_absolute():
            data_dir = (root / data_dir).resolve()
        mode = merged.get("SCENETRACE_MODE") or defaults["mode"]
        if mode not in ("fixture", "recorded_real", "live"):
            raise ValueError(f"unsupported SCENETRACE_MODE {mode!r}")
        return cls(
            root=root,
            data_dir=data_dir,
            default_mode=mode,  # type: ignore[arg-type]
            model_api_key=merged.get("MODEL_API_KEY") or None,
            demo_access_code=merged.get("SCENETRACE_DEMO_ACCESS_CODE") or None,
            enable_secondary_review=_flag(merged.get("SCENETRACE_ENABLE_SECONDARY_REVIEW"), defaults["enable_secondary_review"]),
            enable_live_uploads=_flag(merged.get("SCENETRACE_ENABLE_LIVE_UPLOADS"), False),
            run_live_tests=_flag(merged.get("SCENETRACE_RUN_LIVE_TESTS"), False),
            public_https=_flag(merged.get("SCENETRACE_PUBLIC_HTTPS"), False),
            bind_host=merged.get("SCENETRACE_HOST") or "127.0.0.1",
            defaults=defaults,
            rates=Rates.load(root / "config" / "rates.json"),
            demo_read_only=_flag(merged.get("SCENETRACE_DEMO_READ_ONLY"), False),
        )
