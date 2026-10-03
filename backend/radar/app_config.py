"""Integration Radar's app config, kept in Crew's per-app config store.

Stored through ``GET/PUT /api/apps/integration-radar/config`` (a whole-object
replace). The backend is the only writer: the Setup tab changes settings via
backend endpoints, which read-modify-write under a lock so concurrent edits
cannot clobber each other.

Shape::

    {
      "manifestPath": "/abs/path/integration-radar.manifest.yaml",
      "schedule": {"timezone": "UTC", "sweepTime": "06:00",
                   "digestEnabled": false, "digestDay": "mon", "digestTime": "09:00"},
      "roles": {"chat": {"candidate": "interface:slack",
                         "destination": {"id": "channel:C123", "name": "#api-changes"},
                         "confirmedAt": "..."},
                "tracker": {"off": true}}
    }
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .gateway import GatewayClient, GatewayError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_SCHEDULE = {
    "timezone": "UTC",
    "sweepTime": "06:00",
    "digestEnabled": False,  # the digest is opt-in
    "digestDay": "mon",
    "digestTime": "09:00",
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate_schedule(schedule: dict[str, Any]) -> list[str]:
    errors = []
    unknown = set(schedule) - set(DEFAULT_SCHEDULE)
    if unknown:
        errors.append(f"unknown schedule keys: {', '.join(sorted(unknown))}")
    for key in ("sweepTime", "digestTime"):
        if key in schedule and not (isinstance(schedule[key], str) and _TIME_RE.match(schedule[key])):
            errors.append(f"{key} must be HH:MM (24-hour)")
    if "digestDay" in schedule and schedule["digestDay"] not in DAYS:
        errors.append(f"digestDay must be one of: {', '.join(DAYS)}")
    if "digestEnabled" in schedule and not isinstance(schedule["digestEnabled"], bool):
        errors.append("digestEnabled must be true or false")
    if "timezone" in schedule:
        try:
            ZoneInfo(str(schedule["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"unknown timezone {schedule['timezone']!r} (use an IANA name such as Europe/London)")
    return errors


class ConfigStore:
    def __init__(self, client: GatewayClient, app_name: str) -> None:
        self._client = client
        self._path = f"/api/apps/{app_name}/config"
        self._lock = asyncio.Lock()
        self._cache: dict[str, Any] | None = None
        self.last_error: str | None = None

    async def load(self) -> dict[str, Any]:
        """The stored config (cached copy if the gateway is unreachable)."""
        try:
            data = await self._client.get(self._path)
            self._cache = data if isinstance(data, dict) else {}
            self.last_error = None
        except GatewayError as e:
            self.last_error = str(e)
            if self._cache is None:
                raise
        return copy.deepcopy(self._cache)

    async def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Read, apply ``mutate`` in place, write back. Serialized; raises GatewayError."""
        async with self._lock:
            data = await self._client.get(self._path)
            data = data if isinstance(data, dict) else {}
            mutate(data)
            await self._client.put(self._path, data)
            self._cache = data
            self.last_error = None
            return copy.deepcopy(data)


def schedule_of(config: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_SCHEDULE, **(config.get("schedule") or {})}


def bindings_of(config: dict[str, Any]) -> dict[str, Any]:
    roles = config.get("roles")
    return roles if isinstance(roles, dict) else {}
