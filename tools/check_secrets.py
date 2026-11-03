from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [re.compile(rb"(?i)bearer\s+[a-z0-9._\-]{24,}"), re.compile(rb"MODEL_API_KEY=[A-Za-z0-9._\-]{16,}"),
            re.compile(rb"MODEL_API_KEY[\"']?\s*:\s*[\"'][A-Za-z0-9._\-]{16,}"),
            re.compile(rb"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9]{24,}")]


def configured_key() -> bytes | None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("MODEL_API_KEY=") and len(line) > len("MODEL_API_KEY=") + 8:
                return line.split("=", 1)[1].strip().encode()
    return None


def main() -> int:
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    files = [ROOT / f.decode() for f in tracked if f]
    files += [p for p in (ROOT / "client" / "dist").rglob("*") if p.is_file()]
    key = configured_key()
    problems = []
    for path in files:
        if not path.is_file() or path.name == ".env":
            continue
        data = path.read_bytes()
        if key and key in data:
            problems.append(f"{path.relative_to(ROOT)}: contains the configured MODEL_API_KEY")
        for pattern in PATTERNS:
            if pattern.search(data):
                problems.append(f"{path.relative_to(ROOT)}: matches {pattern.pattern[:24]!r}")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if re.search(r"(?m)^MODEL_API_KEY=\S", example):
        problems.append(".env.example: MODEL_API_KEY must stay blank")
    for line in problems:
        print("SECRET CHECK:", line)
    print(f"secret check: {len(files)} files scanned, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
