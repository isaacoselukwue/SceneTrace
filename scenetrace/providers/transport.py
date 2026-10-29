"""HTTPX transport for Meta Model API (https://api.meta.ai/v1).

The only live implementation of the provider wire protocol. No implicit retries:
`metered_call` and the adapters own retry decisions so every attempt is metered.
Errors are sanitised; the key is never logged or echoed.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .base import CancelToken, ProviderError, ProviderUnavailable
from .sse import SSEDecodeError, SSEDecoder, decode_json_event

TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed", "error")
RETRYABLE_STATUS = {429, 500, 502, 503}


@dataclass
class HTTPResult:
    status: int
    json: dict[str, Any]
    request_id: str | None
    elapsed_ms: int


@dataclass
class StreamResult:
    status: int
    events: list[dict[str, Any]]
    terminal: str | None  # one of TERMINAL_EVENTS, or None when the stream ended without one
    response: dict[str, Any]  # the response object from the terminal event, if any
    response_id: str | None
    usage: dict[str, Any] | None
    request_id: str | None
    elapsed_ms: int


def _retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def error_from_response(resp: httpx.Response) -> ProviderError:
    request_id = resp.headers.get("x-request-id")
    try:
        body = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        body = {}
    code = str(body.get("code") or body.get("type") or f"HTTP_{resp.status_code}")
    status = resp.status_code
    kwargs = dict(http_status=status, request_id=request_id, retry_after_s=_retry_after(resp.headers))
    if status in (401, 403) or (status == 404 and code == "model_not_found"):
        return ProviderUnavailable(code, retryable=False, **kwargs)
    if status in RETRYABLE_STATUS:
        # 5xx work may already have run; treat billing as unknown. 429 is rejected before work.
        return ProviderError(code, retryable=True, ambiguous=status >= 500, **kwargs)
    if status == 504:
        return ProviderError(code, retryable=False, ambiguous=True, **kwargs)
    return ProviderError(code, retryable=False, ambiguous=False, **kwargs)


class MetaTransport:
    def __init__(self, base_url: str, api_key: str | None, *, connect_s: float = 10.0, read_s: float = 120.0,
                 write_s: float = 120.0, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ProviderUnavailable("NO_API_KEY")
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=connect_s, read=read_s, write=write_s, pool=connect_s),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise ProviderError("CONNECT_FAILED", retryable=True, ambiguous=False) from exc
        except httpx.ConnectTimeout as exc:
            raise ProviderError("CONNECT_TIMEOUT", retryable=True, ambiguous=False) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("READ_TIMEOUT", retryable=False, ambiguous=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("TRANSPORT_ERROR", retryable=False, ambiguous=True) from exc

    def list_models(self) -> list[str]:
        resp = self._request("GET", "/models")
        if resp.status_code != 200:
            raise error_from_response(resp)
        return sorted(str(m.get("id")) for m in resp.json().get("data", []) if isinstance(m, dict))

    def upload_file(self, path: Path, *, media_type: str, expires_after_s: int | None = None) -> HTTPResult:
        data: dict[str, str] = {"purpose": "user_data"}
        if expires_after_s is not None:
            data["expires_after[anchor]"] = "created_at"
            data["expires_after[seconds]"] = str(int(expires_after_s))
        started = time.monotonic()
        with path.open("rb") as fh:
            resp = self._request("POST", "/files", data=data, files={"file": (path.name, fh, media_type)})
        if resp.status_code != 200:
            raise error_from_response(resp)
        return HTTPResult(resp.status_code, resp.json(), resp.headers.get("x-request-id"),
                          int((time.monotonic() - started) * 1000))

    def delete_file(self, file_id: str) -> int:
        resp = self._request("DELETE", f"/files/{file_id}")
        if resp.status_code not in (200, 404):
            raise error_from_response(resp)
        return resp.status_code

    def count_input_tokens(self, body: dict[str, Any]) -> HTTPResult:
        allowed = {k: v for k, v in body.items() if k in ("model", "input", "instructions", "tools", "previous_response_id",
                                                          "text", "reasoning", "tool_choice", "parallel_tool_calls")}
        started = time.monotonic()
        resp = self._request("POST", "/responses/input_tokens", json=allowed)
        if resp.status_code != 200:
            raise error_from_response(resp)
        return HTTPResult(resp.status_code, resp.json(), resp.headers.get("x-request-id"),
                          int((time.monotonic() - started) * 1000))

    def create_response(self, body: dict[str, Any]) -> HTTPResult:
        if body.get("stream"):
            raise ValueError("use stream_response for streaming requests")
        if body.get("tool_choice", "auto") != "auto":
            raise ValueError("Meta Model API supports only tool_choice=auto")
        started = time.monotonic()
        resp = self._request("POST", "/responses", json=body)
        if resp.status_code != 200:
            raise error_from_response(resp)
        return HTTPResult(resp.status_code, resp.json(), resp.headers.get("x-request-id"),
                          int((time.monotonic() - started) * 1000))

    def stream_response(self, body: dict[str, Any], *, cancel: CancelToken | None = None,
                        on_event: Callable[[dict[str, Any]], None] | None = None) -> StreamResult:
        """Stream a Responses request to its terminal event.

        Returns partial events when the stream ends without a terminal event
        (terminal=None); callers must record coverage as incomplete. Raises a
        ProviderError(ambiguous=True) for a disconnect after the request started.
        """
        if body.get("tool_choice", "auto") != "auto":
            raise ValueError("Meta Model API supports only tool_choice=auto")
        payload = dict(body, stream=True)
        started = time.monotonic()
        decoder = SSEDecoder()
        events: list[dict[str, Any]] = []
        terminal: str | None = None
        request_id: str | None = None
        try:
            with self._client.stream("POST", "/responses", json=payload, headers={"Accept": "text/event-stream"}) as resp:
                request_id = resp.headers.get("x-request-id")
                if resp.status_code != 200:
                    resp.read()
                    raise error_from_response(resp)
                for chunk in resp.iter_bytes():
                    if cancel is not None and cancel.cancelled:
                        raise ProviderError("CANCELLED_BY_USER", ambiguous=True, request_id=request_id)
                    for sse in decoder.feed(chunk):
                        event = decode_json_event(sse)
                        if event is None:
                            continue
                        events.append(event)
                        if on_event is not None:
                            on_event(event)
                        if event.get("type") in TERMINAL_EVENTS:
                            terminal = str(event["type"])
                    if terminal is not None:
                        break
                if terminal is None:
                    for sse in decoder.close():
                        event = decode_json_event(sse)
                        if event is not None:
                            events.append(event)
                            if event.get("type") in TERMINAL_EVENTS:
                                terminal = str(event["type"])
        except SSEDecodeError as exc:
            raise ProviderError(str(exc), ambiguous=True, request_id=request_id) from exc
        except httpx.ConnectError as exc:
            raise ProviderError("CONNECT_FAILED", retryable=True, ambiguous=False) from exc
        except httpx.ConnectTimeout as exc:
            raise ProviderError("CONNECT_TIMEOUT", retryable=True, ambiguous=False) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("STREAM_READ_TIMEOUT", ambiguous=True, request_id=request_id) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("STREAM_DISCONNECTED", ambiguous=True, request_id=request_id) from exc
        elapsed = int((time.monotonic() - started) * 1000)
        response: dict[str, Any] = {}
        if terminal == "error":
            err = events[-1]
            code = str(err.get("code") or (err.get("error") or {}).get("code") or "STREAM_ERROR")
            raise ProviderError(code, retryable=code == "server_shutting_down", ambiguous=True, request_id=request_id)
        if terminal is not None:
            response = events[-1].get("response") or {}
            if terminal == "response.failed":
                code = str((response.get("error") or {}).get("code") or "RESPONSE_FAILED")
                raise ProviderError(code, ambiguous=True, request_id=request_id)
        response_id = response.get("id")
        if response_id is None:
            for event in events:
                if event.get("type") == "response.created":
                    response_id = (event.get("response") or {}).get("id")
                    break
        return StreamResult(200, events, terminal, response, response_id, response.get("usage"), request_id, elapsed)


def output_text(response: dict[str, Any]) -> str:
    """Concatenate output_text parts of message items in a non-streaming Responses object."""
    parts: list[str] = []
    for item in response.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text":
                    parts.append(str(content.get("text", "")))
    return "".join(parts)
