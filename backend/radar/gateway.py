"""Minimal client for the Crew gateway, authenticated as this app.

Auth: read ``$KIROCREW_HOME/apps/<app>/.app_secret``, exchange it at
``POST /api/apps/<app>/token`` (header ``X-App-Secret``) for a token, and send
the token as the cookie ``mc_token_<gateway_port>``. On a 401/403 the token is
re-exchanged once (the secret is re-read too: a reinstall rotates it).

The gateway only lets this app reach the paths listed in ``permissions.api``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .settings import Settings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15


class GatewayError(Exception):
    """The gateway could not be reached or refused the request. Safe to show."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GatewayClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS))
        return self._session

    async def _exchange(self) -> str:
        try:
            secret = self.settings.app_secret_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise GatewayError("the app secret is missing; reinstall or re-enable the app") from None
        url = f"{self.settings.gateway_origin}/api/apps/{self.settings.app_name}/token"
        try:
            async with self._http().post(url, headers={"X-App-Secret": secret}) as resp:
                if resp.status != 200:
                    raise GatewayError(f"token exchange refused (HTTP {resp.status})", resp.status)
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise GatewayError(f"gateway unreachable at {self.settings.gateway_origin}: {e.__class__.__name__}") from None
        except asyncio.TimeoutError:
            raise GatewayError("gateway timed out during token exchange") from None
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise GatewayError("token exchange returned no token")
        return token

    async def _ensure_token(self, *, refresh: bool = False, stale: str | None = None) -> str:
        async with self._token_lock:
            # Another caller may already have refreshed the token we found stale.
            if self._token is None or (refresh and self._token == stale):
                self._token = await self._exchange()
            return self._token

    async def request(self, method: str, path: str, *, json: Any = None) -> Any:
        """Send a request; return the parsed JSON body. Raises GatewayError."""
        token = await self._ensure_token()
        for attempt in (1, 2):
            cookie = f"mc_token_{self.settings.gateway_port}={token}"
            try:
                async with self._http().request(
                    method, f"{self.settings.gateway_origin}{path}", json=json, headers={"Cookie": cookie}
                ) as resp:
                    if resp.status in (401, 403) and attempt == 1:
                        token = await self._ensure_token(refresh=True, stale=token)
                        continue
                    if not 200 <= resp.status < 300:
                        text = (await resp.text())[:200]
                        raise GatewayError(f"{method} {path} failed (HTTP {resp.status}): {text}", resp.status)
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise GatewayError(f"gateway unreachable for {method} {path}: {e.__class__.__name__}") from None
            except asyncio.TimeoutError:
                raise GatewayError(f"gateway timed out for {method} {path}") from None
        raise GatewayError(f"{method} {path} refused after re-authenticating", 403)

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def put(self, path: str, body: Any) -> Any:
        return await self.request("PUT", path, json=body)

    async def post(self, path: str, body: Any) -> Any:
        return await self.request("POST", path, json=body)
