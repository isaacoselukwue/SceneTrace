from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def command_version(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        text = result.stdout or result.stderr
        return text.splitlines()[0] if text else f"exit={result.returncode}"
    except (OSError, subprocess.TimeoutExpired):
        return "check_failed"


def main() -> None:
    report = {
        "python": sys.version.split()[0],
        "ffmpeg": command_version(["ffmpeg", "-version"]),
        "ffprobe": command_version(["ffprobe", "-version"]),
        "node": command_version(["node", "--version"]),
        "npm": command_version(["npm", "--version"]),
        "git": command_version(["git", "--version"]),
        "model_api_key_present": bool(os.environ.get("MODEL_API_KEY")),
        "pack_root": str(Path(__file__).resolve().parents[1]),
        "live_provider_test": "not_run",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
