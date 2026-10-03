"""Discovery of the tools a team already has in Crew, and role resolution.

Integration Radar has four optional roles: tracker, wiki, chat and repo. Roles
bind only to what Crew already has configured: MCP servers, and messaging
interfaces (Slack, Teams, Discord, Telegram, Webex). Nothing is set up here.

Classification is deterministic: an MCP server is a candidate for a role when
its name or tool names carry that role's vocabulary (one server can serve
several roles, e.g. issues + pages + repositories). Crew's own servers are not
candidates. For chat, a Crew interface is preferred over an MCP server for the
same tool, and only interfaces can deliver in v0.1 (the courier's send_message
targets interfaces).

Resolution per role, re-run on every sweep:
  bound         the user confirmed a candidate and it is present and usable;
  degraded      the user confirmed a candidate that vanished or stopped working;
  off           no usable candidate (the dashboard is the fallback);
  proposed      exactly one usable candidate; confirm it (and its destination);
  needs_choice  two or more usable candidates; the user must choose.
A role the user explicitly switched off stays ``off``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .gateway import GatewayClient, GatewayError

ROLES = ("tracker", "wiki", "chat", "repo")
INTERFACES = ("slack", "teams", "discord", "telegram", "webex")
INTERNAL_SERVER_PREFIX = "kirocrew-"

# Words in a server name or tool name that signal each role.
_ROLE_WORDS = {
    "tracker": {"issue", "issues", "ticket", "tickets", "jira", "linear", "workitem", "workitems", "epic", "bug"},
    "wiki": {"wiki", "confluence", "notion", "page", "pages"},
    "repo": {"repository", "repositories", "repo", "repos", "pullrequest", "pull", "commit", "commits",
             "branch", "branches", "github", "gitlab", "bitbucket"},
    "chat": {"slack", "teams", "discord", "telegram", "webex", "channel", "channels", "chat"},
}


def _words(name: str) -> set[str]:
    """'getConfluencePage' -> {'get', 'confluence', 'page'}; 'create_pull_request' -> {...}."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    parts = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]
    words = set(parts)
    words.update(a + b for a, b in zip(parts, parts[1:]))  # "pull request" -> "pullrequest"
    return words


@dataclass(frozen=True)
class Candidate:
    id: str  # "mcp:<server>" or "interface:<name>"
    kind: str  # "mcp" or "interface"
    name: str
    usable: bool
    status: str  # mcp: ok | needs_auth | error | disabled; interface: connected | not_connected
    evidence: tuple[str, ...] = ()
    deliverable: bool = True  # can this candidate actually carry the role's outbound work in v0.1?
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name, "usable": self.usable, "status": self.status,
                "evidence": list(self.evidence), "deliverable": self.deliverable, "note": self.note}


@dataclass
class Detection:
    candidates: dict[str, list[Candidate]] = field(default_factory=lambda: {r: [] for r in ROLES})
    interfaces: dict[str, dict[str, Any]] = field(default_factory=dict)  # raw interface config, for destinations
    errors: list[str] = field(default_factory=list)
    detected_at: str = ""

    def find(self, role: str, candidate_id: str) -> Candidate | None:
        return next((c for c in self.candidates.get(role, []) if c.id == candidate_id), None)


def classify_mcp(servers: list[dict[str, Any]]) -> dict[str, list[Candidate]]:
    out: dict[str, list[Candidate]] = {r: [] for r in ROLES}
    for server in servers:
        name = str(server.get("name", ""))
        if not name or name.startswith(INTERNAL_SERVER_PREFIX):
            continue
        tools = [str(t) for t in server.get("tools") or []]
        enabled = server.get("enabled", True) is not False
        status = str(server.get("status") or "unknown")
        if not enabled:
            status = "disabled"
        usable = enabled and status == "ok"
        name_words = _words(name)
        for role, vocab in _ROLE_WORDS.items():
            evidence = [t for t in tools if _words(t) & vocab]
            if not evidence and not (name_words & vocab):
                continue
            chat_note = "MCP chat servers cannot deliver yet; use the Crew messaging interface" if role == "chat" else ""
            out[role].append(Candidate(
                id=f"mcp:{name}", kind="mcp", name=name, usable=usable, status=status,
                evidence=tuple(evidence[:3]), deliverable=role != "chat",
                note=chat_note or ("" if usable else f"server status is {status}"),
            ))
    return out


def interface_candidates(configs: dict[str, dict[str, Any]]) -> list[Candidate]:
    out = []
    for name in INTERFACES:
        cfg = configs.get(name)
        if not cfg or not (cfg.get("configured") or cfg.get("connected")):
            continue
        connected = bool(cfg.get("connected"))
        out.append(Candidate(
            id=f"interface:{name}", kind="interface", name=name, usable=connected,
            status="connected" if connected else "not_connected",
            note="" if connected else (cfg.get("connect_error") or "configured but not connected"),
        ))
    return out


def build_detection(servers: list[dict[str, Any]], interface_configs: dict[str, dict[str, Any]]) -> Detection:
    detection = Detection(candidates=classify_mcp(servers), interfaces=interface_configs)
    interfaces = interface_candidates(interface_configs)
    # Prefer the Crew interface when the same tool is also an MCP server.
    families = {c.name for c in interfaces}
    detection.candidates["chat"] = interfaces + [
        c for c in detection.candidates["chat"] if not (_words(c.name) & families)
    ]
    return detection


async def detect(client: GatewayClient, stamp: str) -> Detection:
    """Probe the gateway. Partial failures are recorded, not raised."""
    errors: list[str] = []
    servers: list[dict[str, Any]] = []
    try:
        result = await client.get("/api/mcp")
        servers = [s for s in result if isinstance(s, dict)] if isinstance(result, list) else []
    except GatewayError as e:
        errors.append(f"MCP servers: {e}")
    configs: dict[str, dict[str, Any]] = {}
    for name in INTERFACES:
        try:
            cfg = await client.get(f"/api/{name}/config")
            if isinstance(cfg, dict):
                configs[name] = cfg
        except GatewayError as e:
            if e.status != 404:  # an interface this Crew build does not have
                errors.append(f"{name} interface: {e}")
    detection = build_detection(servers, configs)
    detection.errors = errors
    detection.detected_at = stamp
    return detection


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleState:
    role: str
    status: str  # bound | degraded | off | proposed | needs_choice
    candidates: tuple[Candidate, ...]
    binding: dict[str, Any] | None = None
    proposal: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "status": self.status, "candidates": [c.to_dict() for c in self.candidates],
                "binding": self.binding, "proposal": self.proposal, "reason": self.reason}


def resolve_role(role: str, detection: Detection, binding: dict[str, Any] | None) -> RoleState:
    candidates = tuple(detection.candidates.get(role, []))
    usable = [c for c in candidates if c.usable and c.deliverable]
    if binding and binding.get("off"):
        return RoleState(role, "off", candidates, binding, reason="switched off in Setup")
    if binding and binding.get("candidate"):
        bound = detection.find(role, binding["candidate"])
        if bound is None:
            return RoleState(role, "degraded", candidates, binding,
                             reason=f"{binding['candidate']} is no longer configured in Crew; outbound work is queued")
        if not (bound.usable and bound.deliverable):
            return RoleState(role, "degraded", candidates, binding,
                             reason=f"{bound.id} is {bound.status}; outbound work is queued")
        return RoleState(role, "bound", candidates, binding)
    if not usable:
        return RoleState(role, "off", candidates, reason="no usable tool in Crew; the dashboard is the fallback")
    if len(usable) == 1:
        return RoleState(role, "proposed", candidates, proposal=usable[0].id,
                         reason="confirm it (and pick a destination) in Setup")
    return RoleState(role, "needs_choice", candidates,
                     reason=f"{len(usable)} tools could fill this role; choose one in Setup")


def resolve_roles(detection: Detection, bindings: dict[str, Any]) -> dict[str, RoleState]:
    return {role: resolve_role(role, detection, bindings.get(role)) for role in ROLES}


# ---------------------------------------------------------------------------
# Chat destinations (always from live lists, never guessed)
# ---------------------------------------------------------------------------

DM = {"id": "dm", "name": "Direct message to the interface owner"}


async def chat_destinations(client: GatewayClient, detection: Detection, candidate_id: str) -> list[dict[str, str]]:
    """Live destinations for a chat interface. Raises ValueError for non-interfaces."""
    if not candidate_id.startswith("interface:"):
        raise ValueError("only Crew messaging interfaces can deliver chat in this version")
    name = candidate_id.split(":", 1)[1]
    if name == "slack":
        channels = await client.get("/api/slack/channels")
        out = []
        for ch in channels if isinstance(channels, list) else []:
            if isinstance(ch, dict) and ch.get("id"):
                out.append(DM if ch["id"] == "dm" else {"id": f"channel:{ch['id']}", "name": str(ch.get("name") or ch["id"])})
        return out or [DM]
    cfg = detection.interfaces.get(name, {})
    # Destinations this interface's own config already allows.
    allowed_keys = {"discord": "allowed_channel_ids", "webex": "allowed_room_ids", "telegram": "allowed_forum_chat_ids"}
    extra = [{"id": f"target:{t}", "name": str(t)} for t in cfg.get(allowed_keys.get(name, ""), []) or []]
    return [DM, *extra]
