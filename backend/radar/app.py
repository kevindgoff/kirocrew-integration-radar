"""aiohttp application factory."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from aiohttp import web

from . import VERSION
from .change_store import ChangeStore
from .fetch import Fetcher
from .manifest import validate_text
from .manifest_store import ManifestStore
from .matcher import rank
from .proxy_auth import proxy_auth_middleware
from .settings import Settings
from .sweep import FetcherFactory, Sweeper

SETTINGS_KEY = web.AppKey("settings", Settings)
MANIFEST_KEY = web.AppKey("manifest", ManifestStore)
CHANGES_KEY = web.AppKey("changes", ChangeStore)
SWEEPER_KEY = web.AppKey("sweeper", Sweeper)
SWEEP_LOCK_KEY = web.AppKey("sweep_lock", asyncio.Lock)
NOW_KEY = web.AppKey("now", Callable)

DB_FILE = "radar.sqlite"


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text='{"error": "body must be JSON"}', content_type="application/json")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='{"error": "body must be a JSON object"}', content_type="application/json")
    return body


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def status(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    return web.json_response(
        {
            "app": settings.app_name,
            "version": VERSION,
            "dataDir": str(settings.data_dir),
        }
    )


async def manifest_status(request: web.Request) -> web.Response:
    return web.json_response(request.app[MANIFEST_KEY].status())


async def manifest_load(request: web.Request) -> web.Response:
    """Validate the manifest at a local path and apply it if valid."""
    body = await _json_body(request)
    path = body.get("path")
    if not isinstance(path, str):
        return web.json_response({"error": "'path' must be a string"}, status=400)
    store = request.app[MANIFEST_KEY]
    store.load_path(path)
    return web.json_response(store.status())


async def manifest_validate(request: web.Request) -> web.Response:
    """Dry run: validate manifest text without applying it."""
    body = await _json_body(request)
    text = body.get("text")
    if not isinstance(text, str):
        return web.json_response({"error": "'text' must be a string"}, status=400)
    return web.json_response(validate_text(text).to_dict())


async def sweep_now(request: web.Request) -> web.Response:
    """Run a sweep of every partner (or ``{"partners": [...]}``) now."""
    manifest = request.app[MANIFEST_KEY].current
    if manifest is None:
        return web.json_response({"error": "no valid connector manifest is loaded"}, status=409)
    partners = None
    # body_exists, not can_read_body: the proxy-auth middleware has already read
    # (and cached) the body, after which can_read_body reports False.
    if request.body_exists:
        body = await _json_body(request)
        partners = body.get("partners")
        if partners is not None and (
            not isinstance(partners, list) or not all(isinstance(p, str) for p in partners)
        ):
            return web.json_response({"error": "'partners' must be a list of partner ids"}, status=400)
    lock = request.app[SWEEP_LOCK_KEY]
    if lock.locked():
        return web.json_response({"error": "a sweep is already running"}, status=409)
    async with lock:
        report = await request.app[SWEEPER_KEY].run(manifest, partners)
    return web.json_response(report.to_dict())


async def list_changes(request: web.Request) -> web.Response:
    """The feed: changes ranked by impact and deadline, each with its connector matches.

    Query: ``status``, ``partner``, and ``matched=1`` to drop changes that affect no connector.
    """
    store = request.app[CHANGES_KEY]
    changes = store.list(status=request.query.get("status"), partner=request.query.get("partner"))
    ranked = rank(changes, request.app[MANIFEST_KEY].current, request.app[NOW_KEY]().date())
    if request.query.get("matched") in ("1", "true"):
        ranked = [r for r in ranked if r.matches]
    return web.json_response({"changes": [r.to_dict() for r in ranked]})


async def list_sources(request: web.Request) -> web.Response:
    store = request.app[CHANGES_KEY]
    return web.json_response({"sources": store.source_health(), "stalePartners": sorted(store.stale_partners())})


async def _close_stores(app: web.Application) -> None:
    app[CHANGES_KEY].close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    settings: Settings,
    *,
    fetcher_factory: FetcherFactory = Fetcher,
    now: Callable[[], datetime] = _utcnow,
) -> web.Application:
    app = web.Application(middlewares=[proxy_auth_middleware(settings.proxy_secret)])
    app[SETTINGS_KEY] = settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app[MANIFEST_KEY] = ManifestStore(settings.data_dir)
    app[CHANGES_KEY] = ChangeStore(settings.data_dir / DB_FILE, now=now)
    app[SWEEPER_KEY] = Sweeper(app[CHANGES_KEY], fetcher_factory=fetcher_factory, now=now)
    app[SWEEP_LOCK_KEY] = asyncio.Lock()
    app[NOW_KEY] = now
    app.on_cleanup.append(_close_stores)
    app.router.add_get("/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/manifest", manifest_status)
    app.router.add_post("/api/manifest/load", manifest_load)
    app.router.add_post("/api/manifest/validate", manifest_validate)
    app.router.add_post("/api/sweep", sweep_now)
    app.router.add_get("/api/changes", list_changes)
    app.router.add_get("/api/sources", list_sources)
    return app
