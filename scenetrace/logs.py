"""Structured JSON logs with secret and payload redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from .ids import utc_now

_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\"',\s]+"),
    re.compile(r"(?i)(model_api_key[\"']?\s*[:=]\s*[\"']?)[^\"',\s]+"),
    re.compile(r"(?i)(st_session=)[^;\s]+"),
    re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{16,}"),
]
FIELDS = ("request_id", "job_id", "clip_id", "stage", "event_code", "model_id", "elapsed_ms", "error_category")


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("data:"):
            text = pattern.sub("data:<redacted>", text)
        else:
            text = pattern.sub(lambda m: m.group(1) + "<redacted>", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {"ts": utc_now(), "severity": record.levelname, "logger": record.name,
                                 "message": redact(record.getMessage())}
        for key in FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = redact(str(value)) if isinstance(value, str) else value
        if record.exc_info:
            entry["error_category"] = entry.get("error_category") or (record.exc_info[0].__name__ if record.exc_info[0] else None)
        return json.dumps(entry, ensure_ascii=True)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("scenetrace")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"scenetrace.{name}")
