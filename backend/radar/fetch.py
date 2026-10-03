"""Outbound HTTP for partner sources, restricted to the manifest's host allowlist.

The only hosts the backend may contact are those of the connector manifest's
source URLs. Every request, and every redirect hop, must be HTTPS to an
allowlisted host; anything else is refused before a connection is made.
Redirects are followed by hand (never by the HTTP client) so each hop is checked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

import aiohttp

from . import VERSION

MAX_REDIRECTS = 5
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30
USER_AGENT = f"IntegrationRadar/{VERSION} (+deterministic changelog watcher)"


class FetchError(Exception):
    """A source could not be fetched. The message is safe to show to users."""


class NotAllowedError(FetchError):
    """The URL (or a redirect target) is outside the outbound allowlist."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # what was requested
    final_url: str  # after redirects
    status: int
    content_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def text(self) -> str:
        charset = "utf-8"
        if "charset=" in self.content_type:
            charset = self.content_type.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


# (url) -> (status, headers, body). Must not follow redirects itself.
RawRequest = Callable[[str], Awaitable[tuple[int, Mapping[str, str], bytes]]]


def check_allowed(url: str, allowed_hosts: Iterable[str]) -> str:
    """Return the URL's host if it may be fetched, else raise NotAllowedError."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise NotAllowedError(f"invalid URL: {url!r}") from None
    if parts.scheme.lower() != "https":
        raise NotAllowedError(f"refused non-HTTPS URL: {url}")
    if parts.username or parts.password:
        raise NotAllowedError("refused URL with embedded credentials")
    if host not in {h.lower() for h in allowed_hosts}:
        raise NotAllowedError(f"refused host outside the manifest allowlist: {host or '(none)'}")
    return host


class Fetcher:
    """Allowlisted fetcher. ``raw_request`` is injectable for tests."""

    def __init__(self, allowed_hosts: Iterable[str], raw_request: RawRequest | None = None) -> None:
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self._raw_request = raw_request
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def fetch(self, url: str) -> FetchResult:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_allowed(current, self.allowed_hosts)
            status, headers, body = await self._request(current)
            if status in (301, 302, 303, 307, 308):
                location = _header(headers, "location")
                if not location:
                    raise FetchError(f"redirect without a Location header from {current}")
                current = urljoin(current, location)
                continue
            if not 200 <= status < 300:
                raise FetchError(f"HTTP {status} from {current}")
            return FetchResult(url, current, status, _header(headers, "content-type") or "", body, dict(headers))
        raise FetchError(f"too many redirects starting at {url}")

    async def _request(self, url: str) -> tuple[int, Mapping[str, str], bytes]:
        if self._raw_request is not None:
            return await self._raw_request(url)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
        try:
            async with self._session.get(url, allow_redirects=False) as resp:
                # content.read(n) returns only what has arrived so far, so read
                # chunks until EOF, enforcing the size cap as we go.
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError(f"response from {url} exceeds {MAX_BYTES} bytes")
                    chunks.append(chunk)
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, b"".join(chunks)
        except asyncio.TimeoutError:
            raise FetchError(f"timed out fetching {url}") from None
        except aiohttp.ClientError as e:
            raise FetchError(f"network error fetching {url}: {e.__class__.__name__}") from None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None
