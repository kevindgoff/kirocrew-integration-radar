"""A fake Crew gateway for tests: token exchange, cookie auth, discovery and app config.

Shapes mirror what a real Crew 0.7.2 gateway returned on 2026-10-03.
"""

from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from radar.settings import Settings

APP = "integration-radar"
INTERFACES = ("slack", "teams", "discord", "telegram", "webex")

# Tool list of a real Atlassian MCP server (issues, pages and Bitbucket repositories).
ATLASSIAN_TOOLS = [
    "atlassianUserInfo", "getConfluencePage", "searchConfluenceUsingCql", "getConfluenceSpaces",
    "createConfluencePage", "updateConfluencePage", "getJiraIssue", "editJiraIssue", "createJiraIssue",
    "getVisibleJiraProjects", "searchJiraIssuesUsingJql", "addCommentToJiraIssue", "getJsmOpsTeamInfo",
    "bitbucketWorkspace", "bitbucketRepository", "bitbucketPullRequest", "bitbucketRepoContent",
]


def mcp_server(name: str, tools: list[str], status: str = "ok", enabled: bool = True) -> dict[str, Any]:
    return {"name": name, "command": "x", "args": [], "status": status, "tools": tools, "error": "",
            "source": "agent", "enabled": enabled}


def interface(connected: bool = False, configured: bool | None = None, **extra) -> dict[str, Any]:
    return {"connected": connected, "configured": connected if configured is None else configured,
            "connect_error": "", "read_only": False, **extra}


class FakeGateway:
    def __init__(self) -> None:
        self.secret = secrets.token_hex(8)
        self.tokens: set[str] = set()
        self.mcp: list[dict[str, Any]] = [
            mcp_server("kirocrew-core", ["send_message", "spawn_run", "learn_add"]),
            mcp_server("kirocrew-cron", ["cron_list"]),
        ]
        self.interfaces: dict[str, dict[str, Any]] = {name: interface() for name in INTERFACES}
        self.slack_channels: list[dict[str, str]] = [{"id": "dm", "name": "Direct Message"}]
        self.config: dict[str, Any] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_paths: dict[str, int] = {}
        self.server: TestServer | None = None
        self.port = 0

    # -- server --------------------------------------------------------------

    def _app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth])
        app.router.add_post(f"/api/apps/{APP}/token", self._token)
        app.router.add_get("/api/mcp", lambda r: web.json_response(self.mcp))
        for name in INTERFACES:
            app.router.add_get(f"/api/{name}/config", self._interface_handler(name))
        app.router.add_get("/api/slack/channels", lambda r: web.json_response(self.slack_channels))
        app.router.add_get(f"/api/apps/{APP}/config", lambda r: web.json_response(self.config))
        app.router.add_put(f"/api/apps/{APP}/config", self._put_config)
        return app

    def _interface_handler(self, name: str):
        async def handler(request: web.Request) -> web.Response:
            return web.json_response(self.interfaces[name])
        return handler

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        self.requests.append((request.method, request.path))
        if request.path in self.fail_paths:
            return web.json_response({"error": "injected"}, status=self.fail_paths[request.path])
        if request.path == f"/api/apps/{APP}/token":
            return await handler(request)
        token = request.cookies.get(f"mc_token_{self.port}")
        if token not in self.tokens:
            return web.json_response({"error": "Token required", "code": "forbidden"}, status=403)
        return await handler(request)

    async def _token(self, request: web.Request) -> web.Response:
        if request.headers.get("X-App-Secret") != self.secret:
            return web.json_response({"error": "bad secret"}, status=403)
        token = secrets.token_hex(16)
        self.tokens.add(token)
        return web.json_response({"token": token})

    async def _put_config(self, request: web.Request) -> web.Response:
        body = await request.json()
        if not isinstance(body, dict):
            return web.json_response({"error": "config must be a JSON object"}, status=400)
        self.config = copy.deepcopy(body)
        return web.json_response({"ok": True, "name": APP})

    async def start(self) -> None:
        self.server = TestServer(self._app())
        await self.server.start_server()
        self.port = self.server.port

    async def close(self) -> None:
        if self.server is not None:
            await self.server.close()

    # -- helpers -------------------------------------------------------------

    def settings(self, home: Path, proxy_secret: str = "test-secret") -> Settings:
        app_dir = home / "apps" / APP
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / ".app_secret").write_text(self.secret)
        return Settings(APP, 0, home, proxy_secret, f"http://127.0.0.1:{self.port}")

    def revoke_tokens(self) -> None:
        self.tokens.clear()

    def count(self, method: str, path: str) -> int:
        return self.requests.count((method, path))
