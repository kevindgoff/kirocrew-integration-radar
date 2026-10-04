"""The Radar service: wires stores, gateway, discovery, sweeps and the scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .agents import AgentRunner
from .analyst import Analyst
from .app_config import ConfigStore, bindings_of
from .change_store import ChangeStore
from .discovery import Detection, RoleState, detect, resolve_roles
from .fetch import Fetcher
from .gateway import GatewayClient, GatewayError
from .lessons import LessonCourier
from .manifest_store import ManifestStore
from .matcher import rank
from .outbound import Outbound
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
        agents: AgentRunner | None = None,
        run_agents: bool = True,
    ) -> None:
        self.settings = settings
        self.now = now
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = ManifestStore(settings.data_dir)
        self.changes = ChangeStore(settings.data_dir / DB_FILE, now=now)
        self.sweeper = Sweeper(self.changes, fetcher_factory=fetcher_factory, now=now)
        self.gateway = gateway or GatewayClient(settings)
        self.config = ConfigStore(self.gateway, settings.app_name)
        self.agents = agents or AgentRunner(self.gateway)
        self.analyst = Analyst(self.changes, self.agents)
        self.courier = LessonCourier(self.changes, self.agents, self.gateway)
        self.outbound = Outbound(self.changes, self.gateway, self.agents)
        self.run_agents = run_agents
        self._analysis_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.detection: Detection | None = None
        self._detected_at_mono = 0.0
        self.last_sweep: SweepReport | None = None
        self._sweep_lock = asyncio.Lock()
        self.scheduler = Scheduler(
            settings.data_dir, load_config=self.config.load, run_sweep=self.scheduled_sweep, now=now,
            run_digest=lambda: self.run_digest("scheduled"),
        )

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
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
        self.background(self.after_sweep(), "after-sweep")
        return report

    # -- background work ------------------------------------------------------

    def background(self, coro, name: str) -> asyncio.Task:
        """Run ``coro`` after the request returns; failures are logged, never raised."""
        task = asyncio.create_task(coro, name=f"radar-{name}")
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("background %s failed: %r", name, t.exception())

        task.add_done_callback(done)
        return task

    async def after_sweep(self) -> None:
        await self.evaluate_alerts()
        if not self.run_agents:
            return
        await self.analyse_pending()
        await self.courier.deliver()
        await self.flush_digests()

    # -- outbound ---------------------------------------------------------------

    def _ranked_all(self) -> list:
        return rank(self.changes.list(), self.manifest.current, self.now().astimezone(timezone.utc).date())

    def partner_names(self) -> dict[str, str]:
        manifest = self.manifest.current
        return {p.id: p.display_name for p in manifest.partners.values()} if manifest else {}

    async def evaluate_alerts(self) -> int:
        """Push dashboard alerts for changes that newly became urgent."""
        if self.manifest.current is None:
            return 0
        return await self.outbound.push_urgent(self._ranked_all(), self.partner_names())

    def render_digest(self) -> str:
        return self.outbound.render(self._ranked_all(), self.partner_names(),
                                    self.now().astimezone(timezone.utc).date())

    async def run_digest(self, trigger: str) -> dict:
        """Render this week's digest and deliver it, keep it in the dashboard, or queue it."""
        if self.manifest.current is None:
            raise NoManifestError("no valid connector manifest is loaded")
        chat = (await self.roles(refresh=True))["chat"]
        if chat.status == "bound" and not self.run_agents:
            raise RuntimeError("agents are disabled, so chat delivery is unavailable")
        return await self.outbound.send_digest(self.render_digest(), chat, trigger)

    async def flush_digests(self) -> int:
        """Deliver digests that were queued while the chat tool was unavailable."""
        if not self.changes.digests(limit=1, status="queued"):
            return 0
        chat = (await self.roles(refresh=True))["chat"]
        return await self.outbound.flush_queued(chat)

    async def analyse_pending(self, change_ids: Iterable[str] | None = None) -> int:
        """Analyse matched changes that have no analysis for their current content."""
        manifest = self.manifest.current
        if manifest is None or not self.run_agents:
            return 0
        async with self._analysis_lock:
            changes = self.changes.list()
            if change_ids is not None:
                wanted = set(change_ids)
                changes = [c for c in changes if c.id in wanted]
            ranked = rank(changes, manifest, self.now().astimezone(timezone.utc).date())
            return await self.analyst.analyse_due(ranked, manifest)

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
