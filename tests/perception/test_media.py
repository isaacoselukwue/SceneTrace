from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scenetrace.config import Settings
from scenetrace.media.frames import extract_source_frames
from scenetrace.media.normalise import normalise_clip, proxy_frame_png
from scenetrace.media.probe import MediaRejected, probe_media

BITS = 12
BLOCK = 40


def _frame(number: int, w: int = 640, h: int = 360) -> bytes:
    img = np.full((h, w, 3), 90, dtype=np.uint8)
    for bit in range(BITS):
        if number >> bit & 1:
            img[0:BLOCK, bit * BLOCK:(bit + 1) * BLOCK] = 255
        else:
            img[0:BLOCK, bit * BLOCK:(bit + 1) * BLOCK] = 0
    return img.tobytes()


def _decode_number(image: Image.Image) -> int:
    arr = np.asarray(image.convert("L"))
    scale = arr.shape[1] / 640
    value = 0
    for bit in range(BITS):
        y, x = int(BLOCK * scale / 2), int((bit * BLOCK + BLOCK / 2) * scale)
        if arr[y, x] > 128:
            value |= 1 << bit
    return value


def _encode(path: Path, numbers: list[int], fps: int, extra: list[str] | None = None, w: int = 640, h: int = 360) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-framerate", str(fps),
           "-i", "pipe:0", *(extra or []), "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, input=b"".join(_frame(n, w, h) for n in numbers), check=True, timeout=120)
    return path


def test_constant_rate_map_is_exact(tmp_path: Path, settings: Settings) -> None:
    src = _encode(tmp_path / "cfr.mp4", list(range(75)), 25)
    probe = probe_media(src, settings=settings)
    assert (probe.width, probe.height, probe.frame_count, probe.variable_frame_rate) == (640, 360, 75, False)
    result = normalise_clip(src, tmp_path / "proxy", probe=probe, settings=settings)
    assert result.frame_count == len(result.frame_map.entries) == 30
    for entry in result.frame_map.entries:
        assert entry.t_ms == entry.proxy_frame_index * 100
        assert entry.source_pts_ms <= entry.t_ms
        assert entry.source_frame_index == (entry.proxy_frame_index * 5) // 2
    for k in (0, 7, 29):
        img = Image.open(__import__("io").BytesIO(proxy_frame_png(result.proxy_path, k, fps=10)))
        assert _decode_number(img) == result.frame_map.entry(k).source_frame_index


def test_variable_rate_map_uses_timestamps(tmp_path: Path, settings: Settings) -> None:
    base = _encode(tmp_path / "base.mp4", list(range(120)), 30)
    vfr = tmp_path / "vfr.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-vf", "select='not(mod(n,3))+between(n,40,55)'",
                    "-fps_mode", "vfr", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(vfr)], check=True, timeout=120)
    probe = probe_media(vfr, settings=settings)
    assert probe.variable_frame_rate
    result = normalise_clip(vfr, tmp_path / "proxy", probe=probe, settings=settings)
    frames = extract_source_frames(vfr, result.frame_map, [e.proxy_frame_index for e in result.frame_map.entries],
                                   tmp_path / "src")
    for entry in result.frame_map.entries:
        drawn = _decode_number(Image.open(frames[entry.proxy_frame_index]))
        assert drawn * 1000 / 30 <= entry.t_ms + 1
        assert drawn == max(d for d in _kept_numbers() if d * 1000 / 30 <= entry.t_ms + 1)
        proxy_img = Image.open(__import__("io").BytesIO(proxy_frame_png(result.proxy_path, entry.proxy_frame_index, fps=10)))
        assert _decode_number(proxy_img) == drawn


def _kept_numbers() -> list[int]:
    return [n for n in range(120) if n % 3 == 0 or 40 <= n <= 55]


def test_rotation_metadata_gives_portrait_proxy(tmp_path: Path, settings: Settings) -> None:
    src = _encode(tmp_path / "land.mp4", list(range(20)), 10)
    rotated = tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(src), "-c", "copy", str(rotated)],
                   check=True, timeout=60)
    probe = probe_media(rotated, settings=settings)
    assert probe.rotation_deg in (90, 270) and (probe.width, probe.height) == (360, 640)
    result = normalise_clip(rotated, tmp_path / "proxy", probe=probe, settings=settings)
    assert (result.width, result.height) == (360, 640)
    frames = extract_source_frames(rotated, result.frame_map, [0], tmp_path / "src")
    assert Image.open(frames[0]).size == (360, 640)


def test_large_source_is_downscaled_with_even_dimensions(tmp_path: Path, settings: Settings) -> None:
    src = _encode(tmp_path / "big.mp4", list(range(10)), 10, w=1921, h=1081) if False else None
    src = tmp_path / "big.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1922x1082:rate=10", "-t", "1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True, timeout=60)
    probe = probe_media(src, settings=settings)
    result = normalise_clip(src, tmp_path / "proxy", probe=probe, settings=settings)
    assert max(result.width, result.height) <= 1280 and result.width % 2 == 0 and result.height % 2 == 0


def test_rejections(tmp_path: Path, settings: Settings) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00\x01not a video" * 1000)
    with pytest.raises(MediaRejected):
        probe_media(junk, settings=settings)
    audio = tmp_path / "audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(audio)],
                   check=True, timeout=60)
    with pytest.raises(MediaRejected) as exc:
        probe_media(audio, settings=settings)
    assert exc.value.code == "NO_VIDEO_STREAM"
    long = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=32x32:rate=1", "-t", "95",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(long)], check=True, timeout=60)
    with pytest.raises(MediaRejected) as exc:
        probe_media(long, settings=settings)
    assert exc.value.code == "DURATION_EXCEEDED"
    webm = tmp_path / "clip.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5", "-t", "1",
                    "-c:v", "libvpx-vp9", str(webm)], check=True, timeout=60)
    with pytest.raises(MediaRejected):
        probe_media(webm, settings=settings)


def test_synthetic_sample_normalises_to_expected_proxy(tmp_path: Path, settings: Settings) -> None:
    sample = Path(__file__).resolve().parents[2] / "samples" / "synthetic" / "synthetic_bench_pickup.mp4"
    probe = probe_media(sample, settings=settings)
    result = normalise_clip(sample, tmp_path / "proxy", probe=probe, settings=settings)
    assert (result.width, result.height, result.frame_count) == (960, 540, 200)
