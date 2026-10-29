"""Opaque server-generated identifiers and time helpers."""
from __future__ import annotations

import datetime as dt
import uuid

PREFIXES = {"clip", "ana", "job", "inv", "ast", "exp", "sess", "call", "sel", "demo", "req", "rf"}


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix}")
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()
