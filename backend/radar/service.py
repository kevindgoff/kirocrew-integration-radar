"""The Radar service: wires stores, gateway, discovery, sweeps and the scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .app_config import ConfigStore, bindings_of
from .change_store import ChangeStore
from .discovery import Detection, RoleState, detect, resolve_roles
from .fetch import Fetcher
from .gateway import GatewayClient, GatewayError
from .manifest_store import ManifestStore
from .scheduler import Scheduler, partners_due
from .settings import Settings
from .sweep import FetcherFactory, Sweeper, SweepReport

log = logging.getLogger(__name__)

DB_FILE = "radar.sqlite"
#: Re-detect when the cached detection is older than this, or had errors.
DETECTION_MAX_AGE_SECONDS = 300


class NoManifestError(Exception):
    pass


class SweepBusyError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Radar:
    def __init__(
        self,
        settings: Settings,
        *,
        fetcher_factory: FetcherFactory = Fetcher,
        now: Callable[[], datetime] = _utcnow,
        gateway: GatewayClient | None = None,
    ) -> None:
        self.settings = settings
        self.now = now
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = ManifestStore(settings.data_dir)
        self.changes = ChangeStore(settings.data_dir / DB_FILE, now=now)
        self.sweeper = Sweeper(self.changes, fetcher_factory=fetcher_factory, now=now)
        self.gateway = gateway or GatewayClient(settings)
        self.config = ConfigStore(self.gateway, settings.app_name)
        self.detection: Detection | None = None
        self._detected_at_mono = 0.0
        self.last_sweep: SweepReport | None = None
        self._sweep_lock = asyncio.Lock()
        self.scheduler = Scheduler(
            settings.data_dir, load_config=self.config.load, run_sweep=self.scheduled_sweep, now=now
        )

    async def close(self) -> None:
        await self.scheduler.stop()
        await self.gateway.close()
        self.changes.close()

    def stamp(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat(timespec="seconds")

    # -- config -------------------------------------------------------------

    async def load_config(self) -> dict[str, Any]:
        """Stored config, or {} if the gateway has never been reachable."""
        try:
            return await self.config.load()
        except GatewayError:
            return {}

    # -- discovery ----------------------------------------------------------

    async def refresh_detection(self) -> Detection:
        detection = await detect(self.gateway, self.stamp())
        self._detected_at_mono = time.monotonic()
        if detection.errors and self.detection is not None and not any(detection.candidates.values()):
            # Total failure (gateway down): keep what we knew, but surface the errors.
            self.detection.errors = detection.errors
            return self.detection
        self.detection = detection
        return detection

    def _detection_stale(self) -> bool:
        return (
            self.detection is None
            or bool(self.detection.errors)
            or time.monotonic() - self._detected_at_mono > DETECTION_MAX_AGE_SECONDS
        )

    async def roles(self, *, refresh: bool = False) -> dict[str, RoleState]:
        if refresh or self._detection_stale():
            await self.refresh_detection()
        config = await self.load_config()
        return resolve_roles(self.detection, bindings_of(config))

    # -- manifest -----------------------------------------------------------

    async def reload_manifest(self, config: dict[str, Any] | None = None) -> None:
        """Re-read the configured manifest file. An invalid file keeps the last valid one."""
        config = await self.load_config() if config is None else config
        path = config.get("manifestPath")
        if isinstance(path, str) and path:
            self.manifest.load_path(path)

    # -- sweeps -------------------------------------------------------------

    async def run_sweep(self, partners: Iterable[str] | None = None, *, wait: bool = False) -> SweepReport:
        if self._sweep_lock.locked() and not wait:
            raise SweepBusyError("a sweep is already running")
        async with self._sweep_lock:
            config = await self.load_config()
            await self.reload_manifest(config)
            try:
                await self.refresh_detection()  # re-detect roles on every sweep
            except Exception as e:  # discovery must never block the sweep
                log.warning("role detection failed: %s", e)
            manifest = self.manifest.current
            if manifest is None:
                raise NoManifestError("no valid connector manifest is loaded")
            report = await self.sweeper.run(manifest, partners)
            self.last_sweep = report
            return report

    async def scheduled_sweep(self) -> SweepReport:
        config = await self.load_config()
        await self.reload_manifest(config)
        manifest = self.manifest.current
        if manifest is None:
            raise NoManifestError("no valid connector manifest is loaded")
        last_success: dict[str, str | None] = {}
        for h in self.changes.source_health():
            prev = last_success.get(h["partner"])
            if h["lastSuccess"] and (prev is None or h["lastSuccess"] > prev):
                last_success[h["partner"]] = h["lastSuccess"]
        poll = {p.id: p.poll for p in manifest.partners.values()}
        return await self.run_sweep(partners_due(poll, last_success, self.now()), wait=True)
