"""aiohttp application factory."""

from __future__ import annotations

from aiohttp import web

from . import VERSION
from .manifest import validate_text
from .manifest_store import ManifestStore
from .proxy_auth import proxy_auth_middleware
from .settings import Settings

SETTINGS_KEY = web.AppKey("settings", Settings)
MANIFEST_KEY = web.AppKey("manifest", ManifestStore)


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


def create_app(settings: Settings) -> web.Application:
    app = web.Application(middlewares=[proxy_auth_middleware(settings.proxy_secret)])
    app[SETTINGS_KEY] = settings
    app[MANIFEST_KEY] = ManifestStore(settings.data_dir)
    app.router.add_get("/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/manifest", manifest_status)
    app.router.add_post("/api/manifest/load", manifest_load)
    app.router.add_post("/api/manifest/validate", manifest_validate)
    return app
