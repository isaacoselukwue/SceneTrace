"""Create the 10 fps tracking proxy and its exact frame map.

Every proxy slot k (t = k * 100 ms on the clip timeline) shows the source frame
being displayed at that time: the last source frame whose presentation time is
not after the slot. Those source frames are selected explicitly by decode index,
decoded once, and written to the encoder (duplicated when the source is slower
than the proxy), so the map is exact for variable frame rate sources too.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..config import Settings
from ..domain import FrameMap, FrameMapEntry
from ..errors import Cancelled
from ..providers.base import CancelToken
from .probe import MediaRejected, ProbeResult
from .proc import MediaToolError, child_env, kill, run, tool, version

PREPROCESSING_VERSION = "proxy-v1:fps10:long1280:h264-crf20:select-exact"


@dataclass(frozen=True)
class NormaliseResult:
    proxy_path: Path
    proxy_sha256: str
    width: int
    height: int
    fps: int
    frame_count: int
    frame_map: FrameMap
    frame_map_path: Path
    frame_map_sha256: str
    command: list[str]
    ffmpeg_version: str
    preprocessing_version: str
    elapsed_ms: int


def proxy_size(width: int, height: int, long_edge: int) -> tuple[int, int]:
    scale = min(Fraction(1), Fraction(long_edge, max(width, height)))
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return w, h


def build_frame_map(probe: ProbeResult, *, fps: int, proxy_w: int, proxy_h: int) -> FrameMap:
    tb = Fraction(probe.time_base)
    first = probe.frame_pts[0]
    rel_ms = [Fraction(p - first) * tb * 1000 for p in probe.frame_pts]
    slots = int(rel_ms[-1] * fps // 1000) + 1
    entries: list[FrameMapEntry] = []
    source = 0
    for k in range(slots):
        t = Fraction(k * 1000, fps)
        while source + 1 < len(rel_ms) and rel_ms[source + 1] <= t:
            source += 1
        entries.append(FrameMapEntry(proxy_frame_index=k, t_ms=round(t), source_frame_index=source,
                                     source_pts=probe.frame_pts[source], source_pts_ms=round(rel_ms[source])))
    return FrameMap(proxy_fps=fps, proxy_width=proxy_w, proxy_height=proxy_h, source_width=probe.width,
                    source_height=probe.height, rotation_deg=probe.rotation_deg, source_time_base=probe.time_base,
                    source_variable_frame_rate=probe.variable_frame_rate, entries=entries)


def select_expression(indices: list[int]) -> str:
    """A select filter for exact decode indices. The sum is built as a balanced tree because
    ffmpeg's expression parser rejects flat chains of more than about 100 terms."""
    def tree(items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        mid = len(items) // 2
        return f"({tree(items[:mid])}+{tree(items[mid:])})"
    if not indices:
        raise ValueError("no frames selected")
    return "select='" + tree([f"eq(n,{i})" for i in indices]) + "'"


def normalise_clip(source: Path, proxy_dir: Path, *, probe: ProbeResult, settings: Settings,
                   cancel: CancelToken | None = None) -> NormaliseResult:
    started = time.monotonic()
    fps = settings.limit("proxy_fps")
    width, height = proxy_size(probe.width, probe.height, settings.limit("proxy_long_edge_px"))
    frame_map = build_frame_map(probe, fps=fps, proxy_w=width, proxy_h=height)
    unique: list[int] = []
    repeats: list[int] = []
    for entry in frame_map.entries:
        if unique and unique[-1] == entry.source_frame_index:
            repeats[-1] += 1
        else:
            unique.append(entry.source_frame_index)
            repeats.append(1)
    proxy_dir.mkdir(parents=True, exist_ok=True)
    proxy_path = proxy_dir / "proxy.mp4"
    ffmpeg = tool("ffmpeg")
    decode = [ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-f", "mov", "-i", str(source),
              "-map", "0:v:0", "-an", "-sn", "-dn",
              "-vf", f"{select_expression(unique)},scale={width}:{height}:flags=area,format=yuv420p",
              "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p", "pipe:1"]
    encode = [ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}",
              "-framerate", str(fps), "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-map_metadata", "-1", "-map_chapters", "-1",
              "-fflags", "+bitexact", "-movflags", "+faststart", "-y", str(proxy_path)]
    frame_bytes = width * height * 3 // 2
    deadline = started + 600
    with tempfile.TemporaryFile() as dec_err, tempfile.TemporaryFile() as enc_err:
        dec = subprocess.Popen(decode, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=dec_err,
                               env=child_env(), close_fds=True, start_new_session=True)
        enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=enc_err,
                               env=child_env(), close_fds=True, start_new_session=True)
        try:
            assert dec.stdout is not None and enc.stdin is not None
            for count in repeats:
                if cancel is not None and cancel.cancelled:
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise MediaToolError("MEDIA_TIMEOUT", "normalise")
                frame = dec.stdout.read(frame_bytes)
                if len(frame) != frame_bytes:
                    raise MediaRejected("CORRUPT_MEDIA", "decoder ended early")
                for _ in range(count):
                    enc.stdin.write(frame)
            enc.stdin.close()
            if enc.wait(timeout=max(5.0, deadline - time.monotonic())) != 0:
                enc_err.seek(0)
                raise MediaToolError("PROXY_ENCODE_FAILED", enc_err.read().decode("utf-8", "replace")[-300:])
            dec.stdout.read()
            dec.wait(timeout=30)
        except BaseException:
            kill(dec)
            kill(enc)
            raise
    check = run([tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-select_streams", "v:0",
                 "-count_frames", "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(proxy_path)],
                timeout_s=60, cancel=cancel)
    stream = json.loads(check.stdout or b"{}").get("streams", [{}])[0]
    if (int(stream.get("width", 0)), int(stream.get("height", 0)), int(stream.get("nb_read_frames", 0))) != (
            width, height, len(frame_map.entries)):
        raise MediaToolError("PROXY_VERIFY_FAILED", json.dumps(stream))
    map_path = proxy_dir / "frame_map.json"
    map_bytes = frame_map.model_dump_json().encode("utf-8")
    map_path.write_bytes(map_bytes)
    return NormaliseResult(
        proxy_path=proxy_path, proxy_sha256=hashlib.sha256(proxy_path.read_bytes()).hexdigest(), width=width,
        height=height, fps=fps, frame_count=len(frame_map.entries), frame_map=frame_map, frame_map_path=map_path,
        frame_map_sha256=hashlib.sha256(map_bytes).hexdigest(),
        command=[" ".join(decode[:4] + ["..."]), "select-exact decode | libx264 encode"], ffmpeg_version=version("ffmpeg"),
        preprocessing_version=PREPROCESSING_VERSION, elapsed_ms=int((time.monotonic() - started) * 1000))


def proxy_frame_png(proxy_path: Path, frame_index: int, *, fps: int, cancel: CancelToken | None = None) -> bytes:
    del fps  # the proxy is constant rate, so the decode index is the proxy frame index
    result = run([tool("ffmpeg"), "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-f", "mov",
                  "-i", str(proxy_path), "-vf", select_expression([frame_index]), "-fps_mode", "passthrough",
                  "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"], timeout_s=60, cancel=cancel)
    if result.returncode != 0 or not result.stdout:
        raise MediaToolError("FRAME_EXTRACT_FAILED", result.stderr[-300:])
    return result.stdout
