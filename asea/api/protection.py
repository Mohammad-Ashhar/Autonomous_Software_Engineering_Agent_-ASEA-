"""Lightweight ASGI protections for the ASEA API.

The in-memory limiter is intentionally per-process. Deployments with multiple
workers should enforce a shared limit at the trusted ingress as well.
"""
from __future__ import annotations

import json
import os
import time

# Keep the clock reference local so tests can control limiter time without
# altering the process-wide clock used by asyncio/AnyIO and HTTP clients.
_monotonic = time.monotonic
from collections import deque
from threading import Lock
from typing import Any


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


async def _json_response(send: Any, status: int, detail: str, headers=()) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *headers,
    ]
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": response_headers,
    })
    await send({"type": "http.response.body", "body": body})


class RequestProtectionMiddleware:
    """Apply bounded request sizes, per-client rate limits and safe headers."""

    def __init__(
        self,
        app,
        *,
        requests_per_window: int | None = None,
        window_seconds: int | None = None,
        max_body_bytes: int | None = None,
        max_clients: int = 10_000,
    ):
        self.app = app
        self.requests_per_window = requests_per_window or _positive_int(
            "ASEA_RATE_LIMIT_REQUESTS", 60
        )
        self.window_seconds = window_seconds or _positive_int(
            "ASEA_RATE_LIMIT_WINDOW_SECONDS", 60
        )
        self.max_body_bytes = max_body_bytes or _positive_int(
            "ASEA_MAX_REQUEST_BODY_BYTES", 2 * 1024 * 1024
        )
        self.max_clients = max_clients
        self._requests: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _allow_request(self, client: str, now: float) -> tuple[bool, int]:
        cutoff = now - self.window_seconds
        with self._lock:
            # Bound memory even if an attacker rotates source addresses.
            if client not in self._requests and len(self._requests) >= self.max_clients:
                oldest = min(
                    self._requests,
                    key=lambda key: self._requests[key][-1]
                    if self._requests[key] else 0,
                )
                self._requests.pop(oldest, None)
            timestamps = self._requests.setdefault(client, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.requests_per_window:
                retry_after = max(
                    1, int(self.window_seconds - (now - timestamps[0]))
                )
                return False, retry_after
            timestamps.append(now)
            return True, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                await _json_response(send, 400, "Invalid Content-Length header.")
                return
            if declared_size < 0 or declared_size > self.max_body_bytes:
                await _json_response(send, 413, "Request body is too large.")
                return

        # Health checks and CORS preflights must remain available.
        if method != "OPTIONS" and path != "/health":
            client_info = scope.get("client")
            client = str(client_info[0]) if client_info else "unknown"
            allowed, retry_after = self._allow_request(client, _monotonic())
            if not allowed:
                await _json_response(
                    send,
                    429,
                    "Rate limit exceeded. Please retry later.",
                    headers=[
                        (b"retry-after", str(retry_after).encode("ascii")),
                    ],
                )
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def secured_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in response_headers}
                safe_headers = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                ]
                response_headers.extend(
                    (key, value)
                    for key, value in safe_headers
                    if key not in existing
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, limited_receive, secured_send)
        except _RequestBodyTooLarge:
            await _json_response(send, 413, "Request body is too large.")


class _RequestBodyTooLarge(Exception):
    pass
