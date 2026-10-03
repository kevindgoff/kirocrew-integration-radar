"""Verification of the gateway's signed reverse-proxy requests.

The gateway forwards ``/apps/integration-radar/api/<x>`` to this backend as
``/api/<x>`` and signs each request with the per-app secret::

    X-KiroCrew-Proxy: <ts>:<hex>
    <hex> = HMAC_SHA256(secret, f"{ts}:{method}:{raw_path_qs}:{sha256_hex(body)}")

Everything except the unsigned ``/health`` probe must carry a fresh, valid
signature. Any failure (no secret, no header, bad format, stale timestamp,
wrong signature) is a 401.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Awaitable, Callable

from aiohttp import web

HEADER = "X-KiroCrew-Proxy"
MAX_SKEW_SECONDS = 60
UNSIGNED_PATHS = frozenset({"/health"})


def sign(secret: str, ts: int, method: str, target: str, body: bytes) -> str:
    """Compute the hex signature the gateway would send. Used by tests too."""
    body_hash = hashlib.sha256(body or b"").hexdigest()
    msg = f"{ts}:{method}:{target}:{body_hash}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def verify(
    header_value: str | None,
    *,
    secret: str,
    method: str,
    target: str,
    body: bytes,
    now: float | None = None,
) -> bool:
    if not secret or not header_value or ":" not in header_value:
        return False
    ts_str, _, sig = header_value.partition(":")
    if not ts_str.isdigit() or not sig:
        return False
    clock = time.time() if now is None else now
    if abs(clock - int(ts_str)) > MAX_SKEW_SECONDS:
        return False
    expected = sign(secret, int(ts_str), method, target, body)
    return hmac.compare_digest(expected.encode(), sig.encode())


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def proxy_auth_middleware(secret: str):
    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
        if request.method in ("GET", "HEAD") and request.path in UNSIGNED_PATHS:
            return await handler(request)
        # Sign over raw_path (the wire request-target, query included), never the
        # decoded path. request.read() caches the body for the handler.
        body = await request.read()
        if not verify(
            request.headers.get(HEADER),
            secret=secret,
            method=request.method,
            target=request.raw_path,
            body=body,
        ):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    return middleware
