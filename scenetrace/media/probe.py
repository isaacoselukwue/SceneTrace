"""Inspect an upload with ffprobe before any expensive work.

Only local MP4/MOV files are read (mov demuxer, file protocol only), so crafted
inputs cannot make the tools fetch network resources.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

from ..config import Settings
from ..errors import AppError
from ..providers.base import CancelToken
from .proc import run, tool

ALLOWED_CODECS = {"h264", "hevc", "mpeg4", "prores", "mjpeg"}
MAX_EDGE_PX = 8192
MAX_PIXELS = 8192 * 4320
MAX_FRAMES = 10_000


class MediaRejected(AppError):
    MESSAGES = {
        "UNSUPPORTED_MEDIA": "Upload an MP4 or MOV video.",
        "CORRUPT_MEDIA": "The video could not be read. It may be damaged.",
        "NO_VIDEO_STREAM": "The file has no video stream.",
        "DURATION_EXCEEDED": "The clip is longer than the supported limit.",
        "DIMENSIONS_EXCEEDED": "The video dimensions exceed the supported limit.",
        "NON_FINITE_METADATA": "The video metadata is invalid.",
    }

    def __init__(self, code: str, diagnostic: str | None = None) -> None:
        super().__init__(code, self.MESSAGES.get(code, "The video was rejected."), status_code=422, diagnostic=diagnostic)


@dataclass(frozen=True)
class ProbeResult:
    container: str
    codec: str
    coded_width: int
    coded_height: int
    width: int  # displayed, after rotation
    height: int
    rotation_deg: int
    duration_ms: int
    fps_num: int
    fps_den: int
    variable_frame_rate: bool
    frame_count: int
    has_audio: bool
    time_base: str
    frame_pts: tuple[int, ...]  # presentation timestamps of decoded frames, in time_base units


def _fraction(text: str | None) -> Fraction | None:
    try:
        value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _rotation(stream: dict) -> int:
    raw = 0.0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            raw = float(side["rotation"])
    if not raw and "rotate" in (stream.get("tags") or {}):
        raw = float(stream["tags"]["rotate"])
    if not math.isfinite(raw):
        raise MediaRejected("NON_FINITE_METADATA", "rotation")
    return int(round(raw)) % 360


def probe_media(path: Path, *, settings: Settings, cancel: CancelToken | None = None) -> ProbeResult:
    base = [tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file", "-f", "mov"]
    info = run(base + ["-show_streams", "-show_format", "-of", "json", str(path)], timeout_s=30, cancel=cancel)
    if info.returncode != 0:
        raise MediaRejected("UNSUPPORTED_MEDIA" if "Invalid data" in info.stderr else "CORRUPT_MEDIA", info.stderr[-300:])
    try:
        data = json.loads(info.stdout)
    except ValueError as exc:
        raise MediaRejected("CORRUPT_MEDIA", "probe json") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaRejected("NO_VIDEO_STREAM")
    codec = str(video.get("codec_name") or "")
    if codec not in ALLOWED_CODECS:
        raise MediaRejected("UNSUPPORTED_MEDIA", f"codec {codec}")
    coded_w, coded_h = int(video.get("width") or 0), int(video.get("height") or 0)
    if coded_w <= 0 or coded_h <= 0:
        raise MediaRejected("NON_FINITE_METADATA", "dimensions")
    if max(coded_w, coded_h) > MAX_EDGE_PX or coded_w * coded_h > MAX_PIXELS:
        raise MediaRejected("DIMENSIONS_EXCEEDED")
    rotation = _rotation(video)
    width, height = (coded_h, coded_w) if rotation in (90, 270) else (coded_w, coded_h)
    time_base = _fraction(video.get("time_base"))
    rate = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    if time_base is None or rate is None:
        raise MediaRejected("NON_FINITE_METADATA", "time base or rate")

    frames = run(base + ["-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp", "-of", "csv=p=0",
                         str(path)], timeout_s=120, cancel=cancel)
    if frames.returncode != 0:
        raise MediaRejected("CORRUPT_MEDIA", frames.stderr[-300:])
    pts: list[int] = []
    for line in frames.stdout.decode("ascii", "replace").splitlines():
        field = line.split(",")[0].strip()
        if field in ("", "N/A"):
            raise MediaRejected("NON_FINITE_METADATA", "frame timestamp")
        pts.append(int(field))
    if not pts:
        raise MediaRejected("CORRUPT_MEDIA", "no decodable frames")
    if len(pts) > MAX_FRAMES:
        raise MediaRejected("DURATION_EXCEEDED", "frame count")
    if any(b <= a for a, b in pairwise(pts)):
        raise MediaRejected("NON_FINITE_METADATA", "non-increasing timestamps")
    deltas = {b - a for a, b in pairwise(pts)}
    variable = len(deltas) > 1 and (max(deltas) - min(deltas)) > max(1, min(deltas) // 50)
    last_delta = min(deltas) if deltas else int(1 / (rate * time_base))
    duration_ms = int((pts[-1] - pts[0] + last_delta) * time_base * 1000)
    if duration_ms <= 0:
        raise MediaRejected("NON_FINITE_METADATA", "duration")
    if duration_ms > settings.limit("max_clip_duration_ms"):
        raise MediaRejected("DURATION_EXCEEDED")
    fmt = data.get("format") or {}
    return ProbeResult(
        container=str(fmt.get("format_name") or "mov"), codec=codec, coded_width=coded_w, coded_height=coded_h,
        width=width, height=height, rotation_deg=rotation, duration_ms=duration_ms, fps_num=rate.numerator,
        fps_den=rate.denominator, variable_frame_rate=variable, frame_count=len(pts),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        time_base=f"{time_base.numerator}/{time_base.denominator}", frame_pts=tuple(pts))
