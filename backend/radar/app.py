"""aiohttp application factory."""

from __future__ import annotations

from aiohttp import web

from . import VERSION
from .proxy_auth import proxy_auth_middleware
from .settings import Settings

SETTINGS_KEY = web.AppKey("settings", Settings)


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


def create_app(settings: Settings) -> web.Application:
    app = web.Application(middlewares=[proxy_auth_middleware(settings.proxy_secret)])
    app[SETTINGS_KEY] = settings
    app.router.add_get("/health", health)
    app.router.add_get("/api/status", status)
    return app
