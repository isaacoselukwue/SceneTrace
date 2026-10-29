"""Assemble the demonstration MP4 from the raw screen recording and its markers.

Marked spans (waiting on live model calls) are sped up as recorded in markers.json; the
on-screen captions say so. The last card is held so the video runs close to, but not over,
the target length.

    python tools/assemble_video.py <raw.webm> <markers.json> <out.mp4> [--target 178]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("markers", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--target", type=float, default=178.0)
    args = parser.parse_args()
    markers = json.loads(args.markers.read_text())
    total = next(m["total"] for m in markers if "total" in m)
    spans = sorted((m for m in markers if "speed" in m), key=lambda m: m["start"])
    pieces, cursor = [], 0.0
    for span in spans:
        pieces.append((cursor, span["start"], 1.0))
        pieces.append((span["start"], span["end"], float(span["speed"])))
        cursor = span["end"]
    pieces.append((cursor, total, 1.0))
    final = sum((b - a) / s for a, b, s in pieces)
    hold = max(0.0, args.target - final)
    filters = []
    for i, (a, b, s) in enumerate(pieces):
        filters.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{s}[v{i}]")
    concat = "".join(f"[v{i}]" for i in range(len(pieces)))
    filters.append(f"{concat}concat=n={len(pieces)}:v=1:a=0,fps=30,tpad=stop_mode=clone:stop_duration={hold:.2f}[out]")
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", str(args.raw), "-filter_complex", ";".join(filters),
           "-map", "[out]", "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-metadata", "title=SceneTrace demonstration", "-metadata", "artist=Isaac Oselukwue",
           str(args.out)]
    subprocess.run(cmd, check=True)
    print(json.dumps({"pieces": [[round(a, 1), round(b, 1), s] for a, b, s in pieces], "content_seconds": round(final, 1),
                      "final_card_hold_seconds": round(hold, 1), "out": str(args.out)}))


if __name__ == "__main__":
    main()
