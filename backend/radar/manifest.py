"""Connector manifest (YAML) loading and validation.

The manifest has two halves: ``partners`` (where announcements come from) and
``connectors`` (what the product uses). Validation runs over the composed YAML
node tree rather than plain dicts, so every issue carries a 1-based line number.
Validation never stops at the first problem; it reports everything it can.

Rules:
  1. Unknown keys are errors (and so are duplicate keys, anchors and aliases).
  2. Partner references resolve; each connector has at least one surface.
  3. ``api.version`` matches the partner's ``versioning`` pattern.
  4. Source URLs are HTTPS; their hosts form the outbound network allowlist.
  5. Keys matching token|secret|password|api_key are rejected.
  6. Legacy and retiring connectors are flagged "migrate or retire" (a notice).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

MAX_MANIFEST_BYTES = 1024 * 1024

SCHEMA_VERSION = 1
VERSIONING = ("major", "dated", "semver", "rolling")
POLL = ("daily", "weekly")
SOURCE_KINDS = (
    "release_notes",
    "changelog",
    "sunset_schedule",
    "feed",
    "csv_schedule",
    "out_of_cycle",
    "api_spec_repo",
)
LIFECYCLE = ("active", "legacy", "retiring")
CRITICALITY = ("high", "medium", "low")
# Per-role hints. Validated when present, never required.
HINT_KEYS = {
    "tracker_component": "str",
    "tracker_project": "str",
    "tracker_labels": "list",
    "wiki_space": "str",
    "wiki_page": "str",
    "chat_channel": "str",
    "repo_path": "str",
}

SECRET_KEY_RE = re.compile(r"token|secret|password|api_key", re.IGNORECASE)
PARTNER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CONNECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VERSION_RES = {
    "major": re.compile(r"^v?\d{1,3}(\.\d+)?$"),  # v21, v24.0 (not a dated v202508)
    "dated": re.compile(r"^v?\d{4}(0[1-9]|1[0-2])$"),  # v202508
    "semver": re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"),
    "rolling": re.compile(r"^\S+$"),  # any single token, e.g. a date or "latest"
}
VERSION_EXAMPLES = {"major": "v21", "dated": "v202508", "semver": "1.4.0", "rolling": "2025-08-01"}

_STR = "tag:yaml.org,2002:str"
_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"
_TIMESTAMP = "tag:yaml.org,2002:timestamp"  # an unquoted 2025-08-01 (rolling versions)
_VERSION_TAGS = (_STR, _INT, _FLOAT, _TIMESTAMP)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    line: int
    path: str
    message: str
    severity: str = "error"  # "error" or "notice"

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "path": self.path, "message": self.message, "severity": self.severity}

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "manifest"
        return f"{where}: {self.path}: {self.message}" if self.path else f"{where}: {self.message}"


@dataclass(frozen=True)
class Source:
    kind: str
    url: str
    host: str


@dataclass(frozen=True)
class Partner:
    id: str
    display_name: str
    versioning: str
    sources: tuple[Source, ...]
    poll: str = "daily"


@dataclass(frozen=True)
class ApiPin:
    version: str
    client: str = ""
    client_version: str = ""


@dataclass(frozen=True)
class Surface:
    kind: str  # "service" or "endpoint"
    name: str
    resources: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Connector:
    id: str
    partner: str
    api: ApiPin
    surfaces: tuple[Surface, ...]
    criticality: str
    lifecycle: str = "active"
    description: str = ""
    owner: str = ""
    hints: dict[str, Any] = field(default_factory=dict)

    @property
    def action(self) -> str:
        """What a matching change asks of this connector."""
        return "fix" if self.lifecycle == "active" else "migrate or retire"


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    product: str
    partners: dict[str, Partner]
    connectors: tuple[Connector, ...]

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """The backend's outbound allowlist: every source URL host, nothing else."""
        return frozenset(s.host for p in self.partners.values() for s in p.sources)

    def connectors_for(self, partner_id: str) -> tuple[Connector, ...]:
        return tuple(c for c in self.connectors if c.partner == partner_id)

    def summary(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "partners": [
                {"id": p.id, "displayName": p.display_name, "versioning": p.versioning,
                 "poll": p.poll, "sources": len(p.sources)}
                for p in self.partners.values()
            ],
            "connectors": [
                {"id": c.id, "partner": c.partner, "version": c.api.version, "lifecycle": c.lifecycle,
                 "criticality": c.criticality, "action": c.action, "surfaces": len(c.surfaces)}
                for c in self.connectors
            ],
            "allowedHosts": sorted(self.allowed_hosts),
        }


@dataclass
class ValidationResult:
    manifest: Manifest | None
    errors: list[Issue]
    notices: list[Issue]

    @property
    def ok(self) -> bool:
        return self.manifest is not None and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "notices": [i.to_dict() for i in self.notices],
            "summary": self.manifest.summary() if self.manifest else None,
        }


# ---------------------------------------------------------------------------
# YAML composition (no aliases, no duplicate keys)
# ---------------------------------------------------------------------------


class _ManifestLoader(yaml.SafeLoader):
    """Composes nodes only, refusing anchors/aliases (no billion-laughs, no cycles)."""

    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "anchors and aliases are not allowed in the manifest", event.start_mark
            )
        event = self.peek_event()
        anchor = getattr(event, "anchor", None)
        if anchor is not None:
            raise yaml.composer.ComposerError(
                None, None, "anchors and aliases are not allowed in the manifest", event.start_mark
            )
        return super().compose_node(parent, index)


def _line(node: Node | None) -> int:
    return node.start_mark.line + 1 if node is not None else 0


def _join(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


class _Validator:
    def __init__(self) -> None:
        self.errors: list[Issue] = []
        self.notices: list[Issue] = []

    def error(self, node: Node | None, path: str, message: str) -> None:
        self.errors.append(Issue(_line(node), path, message))

    def notice(self, node: Node | None, path: str, message: str) -> None:
        self.notices.append(Issue(_line(node), path, message, "notice"))

    # -- structural helpers -------------------------------------------------

    def mapping(
        self,
        node: Node,
        path: str,
        *,
        required: tuple[str, ...] = (),
        optional: tuple[str, ...] = (),
        free_keys: bool = False,
    ) -> dict[str, tuple[Node, Node]] | None:
        """Check a mapping's keys. Returns {key: (key_node, value_node)}.

        With ``free_keys`` the keys are ids (e.g. partner ids) rather than field
        names, so unknown-key checks are skipped but the secret check still runs.
        """
        if not isinstance(node, MappingNode):
            self.error(node, path, "expected a mapping")
            return None
        out: dict[str, tuple[Node, Node]] = {}
        allowed = set(required) | set(optional)
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.tag != _STR:
                self.error(key_node, path, "keys must be plain strings")
                continue
            key = key_node.value
            kpath = _join(path, key)
            if SECRET_KEY_RE.search(key):
                self.error(key_node, kpath, "credential-like keys are not allowed; the manifest must hold no secrets")
                continue
            if key in out:
                self.error(key_node, kpath, f"duplicate key (first defined on line {_line(out[key][0])})")
                continue
            if not free_keys and key not in allowed:
                self.error(key_node, kpath, f"unknown key; expected one of: {', '.join(sorted(allowed))}")
                continue
            out[key] = (key_node, value_node)
        for key in required:
            if key not in out:
                self.error(node, _join(path, key), "missing required key")
        return out

    def sequence(self, node: Node, path: str, *, min_items: int = 0) -> list[Node] | None:
        if not isinstance(node, SequenceNode):
            self.error(node, path, "expected a list")
            return None
        if len(node.value) < min_items:
            self.error(node, path, f"needs at least {min_items} item{'s' if min_items != 1 else ''}")
        return list(node.value)

    def text(self, node: Node, path: str, *, allow_empty: bool = False) -> str | None:
        if not isinstance(node, ScalarNode) or node.tag != _STR:
            self.error(node, path, "expected a string")
            return None
        if not allow_empty and not node.value.strip():
            self.error(node, path, "must not be empty")
            return None
        return node.value

    def choice(self, node: Node, path: str, options: tuple[str, ...]) -> str | None:
        value = self.text(node, path)
        if value is not None and value not in options:
            self.error(node, path, f"{value!r} is not one of: {', '.join(options)}")
            return None
        return value

    def string_list(self, node: Node, path: str) -> tuple[str, ...]:
        items = self.sequence(node, path)
        if items is None:
            return ()
        out = []
        for i, item in enumerate(items):
            value = self.text(item, _join(path, i))
            if value is not None:
                out.append(value)
        return tuple(out)

    # -- sections -----------------------------------------------------------

    def root(self, node: Node | None) -> Manifest | None:
        if node is None:
            self.error(None, "", "manifest is empty")
            return None
        top = self.mapping(
            node, "", required=("schema_version", "product", "partners", "connectors")
        )
        if top is None:
            return None

        schema_version = None
        if "schema_version" in top:
            v = top["schema_version"][1]
            if isinstance(v, ScalarNode) and v.tag == _INT and v.value == str(SCHEMA_VERSION):
                schema_version = SCHEMA_VERSION
            else:
                self.error(v, "schema_version", f"must be {SCHEMA_VERSION}")

        product = self.text(top["product"][1], "product") if "product" in top else None

        partners: dict[str, Partner] = {}
        if "partners" in top:
            partners = self.partners(top["partners"][1])

        connectors: list[Connector] = []
        if "connectors" in top:
            connectors = self.connectors(top["connectors"][1], partners, top.get("partners"))

        if self.errors or schema_version is None or product is None:
            return None
        return Manifest(schema_version, product, partners, tuple(connectors))

    def partners(self, node: Node) -> dict[str, Partner]:
        entries = self.mapping(node, "partners", free_keys=True)
        out: dict[str, Partner] = {}
        if entries is None:
            return out
        if not entries:
            self.error(node, "partners", "define at least one partner")
        for pid, (key_node, value_node) in entries.items():
            path = _join("partners", pid)
            if not PARTNER_ID_RE.match(pid):
                self.error(key_node, path, "partner id must be lowercase letters, digits, '_' or '-'")
                continue
            partner = self.partner(pid, value_node, path)
            if partner is not None:
                out[pid] = partner
        return out

    def partner(self, pid: str, node: Node, path: str) -> Partner | None:
        f = self.mapping(node, path, required=("display_name", "versioning", "sources"), optional=("poll",))
        if f is None:
            return None
        display_name = self.text(f["display_name"][1], _join(path, "display_name")) if "display_name" in f else None
        versioning = self.choice(f["versioning"][1], _join(path, "versioning"), VERSIONING) if "versioning" in f else None
        poll = self.choice(f["poll"][1], _join(path, "poll"), POLL) if "poll" in f else "daily"
        sources: list[Source] = []
        if "sources" in f:
            spath = _join(path, "sources")
            items = self.sequence(f["sources"][1], spath, min_items=1) or []
            for i, item in enumerate(items):
                src = self.source(item, _join(spath, i))
                if src is not None:
                    sources.append(src)
        if display_name is None or versioning is None or poll is None or not sources:
            return None
        return Partner(pid, display_name, versioning, tuple(sources), poll)

    def source(self, node: Node, path: str) -> Source | None:
        f = self.mapping(node, path, required=("kind", "url"))
        if f is None:
            return None
        kind = self.choice(f["kind"][1], _join(path, "kind"), SOURCE_KINDS) if "kind" in f else None
        url = None
        host = ""
        if "url" in f:
            unode = f["url"][1]
            upath = _join(path, "url")
            url = self.text(unode, upath)
            if url is not None:
                host = self.check_url(unode, upath, url)
                if not host:
                    url = None
        if kind is None or url is None:
            return None
        return Source(kind, url, host)

    def check_url(self, node: Node, path: str, url: str) -> str:
        """Return the lowercased host of a valid HTTPS URL, else '' (after reporting)."""
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname or ""
        except ValueError:
            self.error(node, path, "not a valid URL")
            return ""
        if parts.scheme.lower() != "https":
            self.error(node, path, "source URLs must use https://")
            return ""
        if not host:
            self.error(node, path, "URL has no host")
            return ""
        if parts.username or parts.password:
            self.error(node, path, "URLs must not embed credentials")
            return ""
        if any(SECRET_KEY_RE.search(k) for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            self.error(node, path, "URLs must not carry credential-like query parameters")
            return ""
        return host.lower()

    def connectors(
        self, node: Node, partners: dict[str, Partner], partners_entry: tuple[Node, Node] | None
    ) -> list[Connector]:
        items = self.sequence(node, "connectors", min_items=1)
        out: list[Connector] = []
        if items is None:
            return out
        declared = set()
        if partners_entry is not None and isinstance(partners_entry[1], MappingNode):
            declared = {k.value for k, _ in partners_entry[1].value if isinstance(k, ScalarNode)}
        seen: dict[str, int] = {}
        for i, item in enumerate(items):
            path = _join("connectors", i)
            conn = self.connector(item, path, partners, declared)
            if conn is None:
                continue
            if conn.id in seen:
                self.error(item, _join(path, "id"), f"duplicate connector id {conn.id!r} (first on line {seen[conn.id]})")
                continue
            seen[conn.id] = _line(item)
            if conn.lifecycle != "active":
                self.notice(item, path, f"connector {conn.id!r} is {conn.lifecycle}: matching changes are flagged 'migrate or retire'")
            out.append(conn)
        return out

    def connector(self, node: Node, path: str, partners: dict[str, Partner], declared: set[str]) -> Connector | None:
        f = self.mapping(
            node,
            path,
            required=("id", "partner", "api", "surfaces", "criticality"),
            optional=("lifecycle", "description", "owner", "hints"),
        )
        if f is None:
            return None
        p = lambda k: _join(path, k)  # noqa: E731

        cid = None
        if "id" in f:
            cid = self.text(f["id"][1], p("id"))
            if cid is not None and not CONNECTOR_ID_RE.match(cid):
                self.error(f["id"][1], p("id"), "connector id must be a slug: lowercase letters, digits, '_' or '-'")
                cid = None

        partner_id = None
        if "partner" in f:
            pnode = f["partner"][1]
            partner_id = self.text(pnode, p("partner"))
            if partner_id is not None and partner_id not in declared:
                known = ", ".join(sorted(declared)) or "none defined"
                self.error(pnode, p("partner"), f"unknown partner {partner_id!r} (defined: {known})")
                partner_id = None

        lifecycle = self.choice(f["lifecycle"][1], p("lifecycle"), LIFECYCLE) if "lifecycle" in f else "active"
        criticality = self.choice(f["criticality"][1], p("criticality"), CRITICALITY) if "criticality" in f else None
        description = self.text(f["description"][1], p("description"), allow_empty=True) if "description" in f else ""
        owner = self.text(f["owner"][1], p("owner"), allow_empty=True) if "owner" in f else ""

        api = None
        if "api" in f:
            api = self.api(f["api"][1], p("api"), partners.get(partner_id) if partner_id else None)

        surfaces: list[Surface] = []
        if "surfaces" in f:
            items = self.sequence(f["surfaces"][1], p("surfaces"), min_items=1) or []
            for i, item in enumerate(items):
                s = self.surface(item, _join(p("surfaces"), i))
                if s is not None:
                    surfaces.append(s)

        hints = self.hints(f["hints"][1], p("hints")) if "hints" in f else {}

        if None in (cid, partner_id, lifecycle, criticality, api, description, owner, hints) or not surfaces:
            return None
        return Connector(
            id=cid, partner=partner_id, api=api, surfaces=tuple(surfaces), criticality=criticality,
            lifecycle=lifecycle, description=description, owner=owner, hints=hints,
        )

    def api(self, node: Node, path: str, partner: Partner | None) -> ApiPin | None:
        f = self.mapping(node, path, required=("version",), optional=("client", "client_version"))
        if f is None:
            return None
        version = None
        if "version" in f:
            vnode = f["version"][1]
            vpath = _join(path, "version")
            # Keep the source text: YAML would read 24.0 as a float, 202508 as an int
            # and 2025-08-01 as a date.
            if isinstance(vnode, ScalarNode) and vnode.tag in _VERSION_TAGS and vnode.value.strip():
                version = vnode.value.strip()
                if partner is not None and not VERSION_RES[partner.versioning].match(version):
                    self.error(
                        vnode, vpath,
                        f"{version!r} does not match partner {partner.id!r} versioning "
                        f"{partner.versioning!r} (e.g. {VERSION_EXAMPLES[partner.versioning]})",
                    )
                    version = None
            else:
                self.error(vnode, vpath, "expected a version string")
        client = self.text(f["client"][1], _join(path, "client"), allow_empty=True) if "client" in f else ""
        client_version = (
            self.text(f["client_version"][1], _join(path, "client_version"), allow_empty=True)
            if "client_version" in f else ""
        )
        if version is None or client is None or client_version is None:
            return None
        return ApiPin(version, client, client_version)

    def surface(self, node: Node, path: str) -> Surface | None:
        f = self.mapping(node, path, optional=("service", "endpoint", "resources", "fields"))
        if f is None:
            return None
        kinds = [k for k in ("service", "endpoint") if k in f]
        if len(kinds) != 1:
            self.error(node, path, "a surface needs exactly one of 'service' or 'endpoint'")
            return None
        kind = kinds[0]
        name = self.text(f[kind][1], _join(path, kind))
        resources = self.string_list(f["resources"][1], _join(path, "resources")) if "resources" in f else ()
        fields_ = self.string_list(f["fields"][1], _join(path, "fields")) if "fields" in f else ()
        if name is None:
            return None
        return Surface(kind, name, resources, fields_)

    def hints(self, node: Node, path: str) -> dict[str, Any] | None:
        f = self.mapping(node, path, optional=tuple(HINT_KEYS))
        if f is None:
            return None
        out: dict[str, Any] = {}
        ok = True
        for key, (_, vnode) in f.items():
            kpath = _join(path, key)
            if HINT_KEYS[key] == "list":
                out[key] = list(self.string_list(vnode, kpath))
            else:
                value = self.text(vnode, kpath)
                if value is None:
                    ok = False
                else:
                    out[key] = value
        return out if ok else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_text(text: str) -> ValidationResult:
    """Parse and validate manifest text. Never raises for bad input."""
    if len(text.encode("utf-8", errors="replace")) > MAX_MANIFEST_BYTES:
        return ValidationResult(None, [Issue(0, "", f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")], [])
    try:
        loader = _ManifestLoader(text)
        try:
            node = loader.get_single_node()
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark is not None else 0
        return ValidationResult(None, [Issue(line, "", f"YAML syntax error: {e.problem or e}")], [])
    except yaml.YAMLError as e:
        return ValidationResult(None, [Issue(0, "", f"YAML error: {e}")], [])

    v = _Validator()
    manifest = v.root(node)
    errors = sorted(v.errors, key=lambda i: i.line)
    notices = sorted(v.notices, key=lambda i: i.line)
    return ValidationResult(manifest if not errors else None, errors, notices)
