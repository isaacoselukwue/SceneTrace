"""Incremental server-sent events decoder for provider streams.

Handles arbitrary chunk boundaries (inside JSON, inside multi-byte UTF-8 and
between CR and LF), several events per read, comments and heartbeats, CR/LF/CRLF
line endings and a stream that ends without a final blank line. Lines are split
on bytes before decoding, so a partial UTF-8 sequence is never decoded early.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str
    id: str | None


class SSEDecodeError(ValueError):
    pass


class SSEDecoder:
    MAX_LINE_BYTES = 8 * 1024 * 1024  # a single SAM frame line with masks stays far below this

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._data: list[str] = []
        self._event: str | None = None
        self._id: str | None = None

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        self._buffer.extend(chunk)
        events: list[SSEEvent] = []
        while True:
            line, consumed = self._next_line(final=False)
            if line is None:
                break
            del self._buffer[:consumed]
            event = self._process_line(line)
            if event is not None:
                events.append(event)
        if len(self._buffer) > self.MAX_LINE_BYTES:
            raise SSEDecodeError("SSE_LINE_TOO_LONG")
        return events

    def close(self) -> list[SSEEvent]:
        """Flush at end of stream. A trailing event without a blank line is still returned."""
        events: list[SSEEvent] = []
        while self._buffer:
            line, consumed = self._next_line(final=True)
            if line is None:
                break
            del self._buffer[:consumed]
            event = self._process_line(line)
            if event is not None:
                events.append(event)
        if self._data:
            events.append(SSEEvent(self._event, "\n".join(self._data), self._id))
            self._data, self._event = [], None
        return events

    def _next_line(self, *, final: bool) -> tuple[bytes | None, int]:
        buf = self._buffer
        for index, byte in enumerate(buf):
            if byte == 0x0A:  # LF
                return bytes(buf[:index]), index + 1
            if byte == 0x0D:  # CR, possibly followed by LF in the next chunk
                if index + 1 < len(buf):
                    return bytes(buf[:index]), index + (2 if buf[index + 1] == 0x0A else 1)
                if final:
                    return bytes(buf[:index]), index + 1
                return None, 0
        if final and buf:
            return bytes(buf), len(buf)
        return None, 0

    def _process_line(self, raw: bytes) -> SSEEvent | None:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SSEDecodeError("SSE_INVALID_UTF8") from exc
        if line == "":
            if not self._data:
                self._event = None
                return None
            event = SSEEvent(self._event, "\n".join(self._data), self._id)
            self._data, self._event = [], None
            return event
        if line.startswith(":"):
            return None  # comment or heartbeat
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id":
            self._id = value
        return None


def decode_json_event(event: SSEEvent) -> dict[str, Any] | None:
    """Return the JSON payload, or None for the [DONE] sentinel and keepalives."""
    payload = event.data.strip()
    if payload == "" or payload == "[DONE]":
        return None
    try:
        value = json.loads(payload)
    except ValueError as exc:
        raise SSEDecodeError("SSE_MALFORMED_JSON") from exc
    if not isinstance(value, dict):
        raise SSEDecodeError("SSE_UNEXPECTED_PAYLOAD")
    if value.get("type") == "keepalive":
        return None
    return value
