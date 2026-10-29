"""Extract original-resolution source frames named by the frame map."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..domain import FrameMap
from ..providers.base import CancelToken
from .normalise import select_expression
from .proc import MediaToolError, run, tool


def extract_source_frames(source: Path, frame_map: FrameMap, proxy_frame_indices: list[int], out_dir: Path, *,
                          cancel: CancelToken | None = None) -> dict[int, Path]:
    """Return {proxy_frame_index: PNG path}. Frames come from the original upload, not the proxy."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {p: frame_map.entry(p).source_frame_index for p in proxy_frame_indices}
    sources = sorted(set(wanted.values()))
    if not sources:
        return {}
    staging = out_dir / ".extract"
    staging.mkdir(exist_ok=True)
    result = run([tool("ffmpeg"), "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-f", "mov",
                  "-i", str(source), "-map", "0:v:0", "-vf", select_expression(sources), "-fps_mode", "passthrough",
                  "-f", "image2", "-start_number", "0", str(staging / "s%06d.png")], timeout_s=300, cancel=cancel)
    produced = sorted(staging.glob("s*.png"))
    if result.returncode != 0 or len(produced) != len(sources):
        shutil.rmtree(staging, ignore_errors=True)
        raise MediaToolError("FRAME_EXTRACT_FAILED", result.stderr[-300:])
    by_source: dict[int, Path] = {}
    for index, path in zip(sources, produced):
        target = out_dir / f"source_{index:06d}.png"
        path.replace(target)
        by_source[index] = target
    shutil.rmtree(staging, ignore_errors=True)
    return {p: by_source[s] for p, s in wanted.items()}
