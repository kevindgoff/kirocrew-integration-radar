"""aiohttp application: HTTP endpoints over the Radar service.

The dashboard reaches these as ``/apps/integration-radar/api/<x>``; the gateway
forwards them here as ``/api/<x>``, signed (see proxy_auth).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from aiohttp import web

from . import VERSION
from .app_config import schedule_of, validate_schedule
from .discovery import ROLES, chat_destinations
from .agents import AgentRunner
from .fetch import Fetcher
from .gateway import GatewayClient, GatewayError
from .manifest import validate_text
from .matcher import RankedChange, rank
from .proxy_auth import proxy_auth_middleware
from .service import NoManifestError, Radar, SweepBusyError
from .settings import Settings
from .sweep import FetcherFactory
from .triage import find_prior_dismissal, lesson_rule, to_markdown

RADAR_KEY = web.AppKey("radar", Radar)
#: The gateway's reverse proxy gives up after 30s; answer before that and keep working.
RESPOND_WITHIN_SECONDS = 25


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text='{"error": "body must be JSON"}', content_type="application/json")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='{"error": "body must be a JSON object"}', content_type="application/json")
    return body


def _radar(request: web.Request) -> Radar:
    return request.app[RADAR_KEY]


# -- basics -------------------------------------------------------------------


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def status(request: web.Request) -> web.Response:
    settings = _radar(request).settings
    return web.json_response({"app": settings.app_name, "version": VERSION, "dataDir": str(settings.data_dir)})


# -- manifest -----------------------------------------------------------------


async def manifest_status(request: web.Request) -> web.Response:
    return web.json_response(_radar(request).manifest.status())


async def manifest_load(request: web.Request) -> web.Response:
    """Load the manifest at ``path`` and remember the path in app config.

    Every sweep re-reads that path; an invalid file keeps the last valid manifest.
    The path is saved only once a manifest from it has validated.
    """
    body = await _json_body(request)
    path = body.get("path")
    if not isinstance(path, str):
        return _error("'path' must be a string", 400)
    radar = _radar(request)
    result = radar.manifest.load_path(path)
    saved = False
    if result.ok:
        try:
            await radar.config.update(lambda c: c.__setitem__("manifestPath", path))
            saved = True
        except GatewayError:
            pass
    return web.json_response({**radar.manifest.status(), "pathSaved": saved})


async def manifest_validate(request: web.Request) -> web.Response:
    """Dry run: validate manifest text without applying it."""
    body = await _json_body(request)
    text = body.get("text")
    if not isinstance(text, str):
        return _error("'text' must be a string", 400)
    return web.json_response(validate_text(text).to_dict())


# -- sweeps and the feed --------------------------------------------------------


async def sweep_now(request: web.Request) -> web.Response:
    """Run a sweep of every partner (or ``{"partners": [...]}``) now."""
    partners = None
    # body_exists, not can_read_body: the proxy-auth middleware has already read
    # (and cached) the body, after which can_read_body reports False.
    if request.body_exists:
        body = await _json_body(request)
        partners = body.get("partners")
        if partners is not None and (
            not isinstance(partners, list) or not all(isinstance(p, str) for p in partners)
        ):
            return _error("'partners' must be a list of partner ids", 400)
    radar = _radar(request)
    task = radar.background(radar.run_sweep(partners), "sweep")
    try:
        report = await asyncio.wait_for(asyncio.shield(task), RESPOND_WITHIN_SECONDS)
    except asyncio.TimeoutError:
        # Still running; the UI refreshes from /api/summary when it finishes.
        return web.json_response({"status": "running"}, status=202)
    except (NoManifestError, SweepBusyError) as e:
        return _error(str(e), 409)
    return web.json_response(report.to_dict())


def _partner_name(radar: Radar, partner_id: str) -> str:
    manifest = radar.manifest.current
    partner = manifest.partners.get(partner_id) if manifest else None
    return partner.display_name if partner else partner_id


def _feed_item(radar: Radar, ranked: RankedChange, dismissed: list) -> dict[str, Any]:
    """A ranked change plus its triage context: the lesson it produced, or a prior dismissal it resembles."""
    item = ranked.to_dict()
    item["partnerName"] = _partner_name(radar, ranked.change.partner)
    item["lesson"] = radar.changes.lesson(ranked.change.id)
    item["analysis"] = radar.changes.analysis(ranked.change.id)
    prior = None
    if ranked.change.status != "dismissed":
        match = find_prior_dismissal(ranked.change, dismissed)
        if match is not None:
            prior = {"changeId": match.id, "title": match.title, "reason": match.status_reason,
                     "lesson": radar.changes.lesson(match.id)}
    item["priorDismissal"] = prior  # shown collapsed, never hidden
    return item


def _rank(radar: Radar, changes) -> list[RankedChange]:
    return rank(changes, radar.manifest.current, radar.now().astimezone(timezone.utc).date())


async def list_changes(request: web.Request) -> web.Response:
    """The feed: changes ranked by impact and deadline, each with its connector matches.

    Query: ``status``, ``partner``, and ``matched=1`` to drop changes that affect no connector.
    """
    radar = _radar(request)
    changes = radar.changes.list(status=request.query.get("status"), partner=request.query.get("partner"))
    ranked = _rank(radar, changes)
    if request.query.get("matched") in ("1", "true"):
        ranked = [r for r in ranked if r.matches]
    dismissed = radar.changes.list(status="dismissed")
    return web.json_response({"changes": [_feed_item(radar, r, dismissed) for r in ranked]})


def _one(radar: Radar, change_id: str) -> RankedChange | None:
    change = radar.changes.get(change_id)
    return _rank(radar, [change])[0] if change else None


async def change_markdown(request: web.Request) -> web.Response:
    """A copyable Markdown write-up of one change (what a team without a tracker pastes)."""
    radar = _radar(request)
    ranked = _one(radar, request.match_info["id"])
    if ranked is None:
        return _error("no such change", 404)
    return web.json_response({"markdown": to_markdown(ranked, _partner_name(radar, ranked.change.partner))})


async def triage(request: web.Request) -> web.Response:
    """POST /api/changes/{id}/{accept|dismiss|snooze|reopen}."""
    radar = _radar(request)
    change_id, action = request.match_info["id"], request.match_info["action"]
    body = await _json_body(request) if request.body_exists else {}
    store = radar.changes
    try:
        if action == "accept":
            store.accept(change_id)
        elif action == "dismiss":
            reason = body.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                return _error("dismissing a change requires a reason", 400)
            change = store.get(change_id)
            if change is None:
                raise KeyError(change_id)
            connectors = [m.connector for m in _rank(radar, [change])[0].matches]
            store.dismiss(change_id, reason,
                          lesson_rule(change, _partner_name(radar, change.partner), reason, connectors))
            if radar.run_agents:
                radar.background(radar.courier.deliver(), "lessons")
        elif action == "snooze":
            until = body.get("until")
            if not isinstance(until, str):
                return _error("'until' must be a date (YYYY-MM-DD)", 400)
            store.snooze(change_id, until)
        elif action == "reopen":
            store.reopen(change_id)
        elif action == "analyze":
            if store.get(change_id) is None:
                raise KeyError(change_id)
            if not radar.run_agents:
                return _error("agents are disabled", 409)
            radar.background(radar.analyse_pending([change_id]), "analyze")
            await asyncio.sleep(0)  # let it mark the analysis as running
            ranked = _one(radar, change_id)
            return web.json_response(_feed_item(radar, ranked, store.list(status="dismissed")), status=202)
        else:
            return _error(f"unknown action {action!r}", 404)
    except KeyError:
        return _error("no such change", 404)
    except ValueError as e:
        return _error(str(e), 400)
    ranked = _one(radar, change_id)
    return web.json_response(_feed_item(radar, ranked, store.list(status="dismissed")))


async def deliver_lessons(request: web.Request) -> web.Response:
    """Retry saving queued dismissal lessons into Crew (through radar-courier)."""
    radar = _radar(request)
    if not radar.run_agents:
        return _error("agents are disabled", 409)
    radar.background(radar.courier.deliver(), "lessons")
    return web.json_response({"status": "running", "pending": len(radar.changes.lessons("pending")),
                              "failed": len(radar.changes.lessons("failed"))}, status=202)


async def digest_preview(request: web.Request) -> web.Response:
    """The digest as it would be sent now, and where it would go."""
    radar = _radar(request)
    if radar.manifest.current is None:
        return _error("no valid connector manifest is loaded", 409)
    chat = (await radar.roles())["chat"]
    return web.json_response({"text": radar.render_digest(), "chat": chat.to_dict()})


async def digest_send(request: web.Request) -> web.Response:
    """Send the digest now: to the bound chat, or kept in the dashboard if chat is unbound."""
    radar = _radar(request)
    task = radar.background(radar.run_digest("manual"), "digest")
    try:
        record = await asyncio.wait_for(asyncio.shield(task), RESPOND_WITHIN_SECONDS)
    except asyncio.TimeoutError:
        return web.json_response({"status": "sending"}, status=202)
    except NoManifestError as e:
        return _error(str(e), 409)
    except RuntimeError as e:
        return _error(str(e), 409)
    return web.json_response(record)


async def list_digests(request: web.Request) -> web.Response:
    return web.json_response({"digests": _radar(request).changes.digests(limit=10)})


async def summary(request: web.Request) -> web.Response:
    """Counts for the nav badge and the header: matched changes still to triage, urgent ones, stale partners."""
    radar = _radar(request)
    ranked = [r for r in _rank(radar, radar.changes.list(status="new")) if r.matches]
    last = radar.last_sweep
    return web.json_response({
        "untriaged": len(ranked),
        "urgent": sum(1 for r in ranked if r.urgent),
        "stalePartners": sorted(radar.changes.stale_partners()),
        "pendingLessons": len(radar.changes.lessons("pending")),
        "failedLessons": len(radar.changes.lessons("failed")),
        "lastSweepAt": last.finished_at if last else None,
        "lastDigest": next(iter(radar.changes.digests(limit=1)), None),
        "manifestLoaded": radar.manifest.current is not None,
    })


async def list_sources(request: web.Request) -> web.Response:
    store = _radar(request).changes
    return web.json_response({"sources": store.source_health(), "stalePartners": sorted(store.stale_partners())})


# -- setup: roles, destinations, schedule ------------------------------------------


async def _setup_payload(radar: Radar, *, refresh: bool = False) -> dict[str, Any]:
    roles = await radar.roles(refresh=refresh)
    config = await radar.load_config()
    return {
        "roles": {name: state.to_dict() for name, state in roles.items()},
        "detection": {"detectedAt": radar.detection.detected_at, "errors": radar.detection.errors},
        "manifestPath": config.get("manifestPath"),
        "manifest": radar.manifest.status(),
        "scheduler": await radar.scheduler.status(),
        "configError": radar.config.last_error,
    }


async def setup(request: web.Request) -> web.Response:
    return web.json_response(await _setup_payload(_radar(request)))


async def setup_detect(request: web.Request) -> web.Response:
    return web.json_response(await _setup_payload(_radar(request), refresh=True))


async def role_destinations(request: web.Request) -> web.Response:
    """Live destinations for a chat candidate: ``?candidate=interface:slack``."""
    role = request.match_info["role"]
    if role != "chat":
        return _error("destinations for this role arrive with ticket and wiki writes (P1)", 400)
    radar = _radar(request)
    candidate_id = request.query.get("candidate", "")
    await radar.roles()
    if radar.detection.find("chat", candidate_id) is None:
        return _error(f"{candidate_id!r} is not a chat candidate in Crew", 404)
    try:
        destinations = await chat_destinations(radar.gateway, radar.detection, candidate_id)
    except ValueError as e:
        return _error(str(e), 400)
    except GatewayError as e:
        return _error(str(e), 502)
    return web.json_response({"candidate": candidate_id, "destinations": destinations})


async def bind_role(request: web.Request) -> web.Response:
    """Bind a role: ``{"candidate": id, "destination": {"id": ...}}``, ``{"off": true}``, or
    ``{"candidate": null}`` to go back to automatic detection."""
    role = request.match_info["role"]
    if role not in ROLES:
        return _error(f"unknown role {role!r}; expected one of: {', '.join(ROLES)}", 404)
    body = await _json_body(request)
    radar = _radar(request)
    unknown = set(body) - {"candidate", "destination", "off"}
    if unknown:
        return _error(f"unknown keys: {', '.join(sorted(unknown))}", 400)

    if body.get("off") is True:
        binding: dict[str, Any] | None = {"off": True, "confirmedAt": radar.stamp()}
    elif "candidate" in body and body["candidate"] is None:
        binding = None
    else:
        candidate_id = body.get("candidate")
        if not isinstance(candidate_id, str):
            return _error("'candidate' must be a candidate id, null, or use {\"off\": true}", 400)
        await radar.roles(refresh=True)  # bind against what Crew has right now
        candidate = radar.detection.find(role, candidate_id)
        if candidate is None:
            return _error(f"{candidate_id!r} is not a {role} candidate in Crew", 404)
        if not candidate.usable:
            return _error(f"{candidate_id} is {candidate.status}; fix it in Crew first", 409)
        if not candidate.deliverable:
            return _error(candidate.note or f"{candidate_id} cannot fill the {role} role yet", 409)
        binding = {"candidate": candidate_id, "confirmedAt": radar.stamp()}
        if role == "chat":
            # Destinations are always picked from the live list, never guessed.
            wanted = body.get("destination")
            wanted_id = wanted.get("id") if isinstance(wanted, dict) else None
            if not isinstance(wanted_id, str):
                return _error("chat needs a destination picked from /api/setup/roles/chat/destinations", 400)
            try:
                live = await chat_destinations(radar.gateway, radar.detection, candidate_id)
            except GatewayError as e:
                return _error(str(e), 502)
            match = next((d for d in live if d["id"] == wanted_id), None)
            if match is None:
                return _error(f"destination {wanted_id!r} is not in {candidate_id}'s current list", 409)
            binding["destination"] = match
        elif "destination" in body:
            return _error("destinations for this role arrive with ticket and wiki writes (P1)", 400)

    def mutate(config: dict[str, Any]) -> None:
        roles = config.setdefault("roles", {})
        if binding is None:
            roles.pop(role, None)
        else:
            roles[role] = binding

    try:
        await radar.config.update(mutate)
    except GatewayError as e:
        return _error(f"could not save the binding: {e}", 502)
    return web.json_response(await _setup_payload(radar))


async def update_schedule(request: web.Request) -> web.Response:
    body = await _json_body(request)
    errors = validate_schedule(body)
    if errors:
        return web.json_response({"errors": errors}, status=400)
    radar = _radar(request)
    before = schedule_of(await radar.load_config())
    try:
        saved = await radar.config.update(lambda c: c.__setitem__("schedule", {**(c.get("schedule") or {}), **body}))
    except GatewayError as e:
        return _error(f"could not save the schedule: {e}", 502)
    after = schedule_of(saved)
    if after["digestEnabled"] and not before["digestEnabled"]:
        radar.scheduler.digest_enabled(after)
    return web.json_response(await radar.scheduler.status())


# -- app ------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    settings: Settings,
    *,
    fetcher_factory: FetcherFactory = Fetcher,
    now: Callable[[], datetime] = _utcnow,
    gateway: GatewayClient | None = None,
    agents: AgentRunner | None = None,
    run_agents: bool = True,
    start_scheduler: bool = False,
) -> web.Application:
    app = web.Application(middlewares=[proxy_auth_middleware(settings.proxy_secret)])
    radar = Radar(settings, fetcher_factory=fetcher_factory, now=now, gateway=gateway, agents=agents,
                  run_agents=run_agents)
    app[RADAR_KEY] = radar

    async def on_startup(app: web.Application) -> None:
        if start_scheduler:
            radar.scheduler.start()

    async def on_cleanup(app: web.Application) -> None:
        await radar.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    r = app.router
    r.add_get("/health", health)
    r.add_get("/api/status", status)
    r.add_get("/api/manifest", manifest_status)
    r.add_post("/api/manifest/load", manifest_load)
    r.add_post("/api/manifest/validate", manifest_validate)
    r.add_post("/api/sweep", sweep_now)
    r.add_get("/api/changes", list_changes)
    r.add_get("/api/changes/{id}/markdown", change_markdown)
    r.add_post("/api/changes/{id}/{action}", triage)
    r.add_get("/api/summary", summary)
    r.add_post("/api/lessons/deliver", deliver_lessons)
    r.add_get("/api/digest/preview", digest_preview)
    r.add_post("/api/digest/send", digest_send)
    r.add_get("/api/digests", list_digests)
    r.add_get("/api/sources", list_sources)
    r.add_get("/api/setup", setup)
    r.add_post("/api/setup/detect", setup_detect)
    r.add_get("/api/setup/roles/{role}/destinations", role_destinations)
    r.add_put("/api/setup/roles/{role}", bind_role)
    r.add_put("/api/setup/schedule", update_schedule)
    return app
