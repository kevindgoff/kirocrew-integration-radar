"""Source adapters: fetched bytes -> RawItems. Pluggable, keyed by source kind and host.

Adapters are deterministic parsers. They never call a model and never follow
instructions found in partner content; everything they extract is data.

Lookup prefers an adapter registered for the exact (kind, host), then a generic
adapter for the kind. A source with no adapter is reported as unsupported.

Schedule adapters share one table interpreter: any table with a version column
and a sunset/expiration and/or deprecation column becomes one ``sunset`` and one
``deprecation`` item per version, depending on which dates are present.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Iterator

from .fetch import FetchResult
from .manifest import Partner, Source
from .normalize import RawItem, canonical_version, clean_text, parse_date

MAX_FEED_ENTRIES = 100


class ParseError(Exception):
    """The fetched content could not be interpreted. The message is safe to show."""


@dataclass(frozen=True)
class ParseContext:
    partner: Partner
    source: Source
    result: FetchResult


@dataclass(frozen=True)
class Adapter:
    name: str
    kind: str
    parse: Callable[[ParseContext], list[RawItem]]
    hosts: tuple[str, ...] = ()  # empty: any host


_REGISTRY: list[Adapter] = []


def register(adapter: Adapter) -> Adapter:
    _REGISTRY.append(adapter)
    return adapter


def adapter_for(kind: str, host: str) -> Adapter | None:
    host = host.lower()
    for adapter in _REGISTRY:
        if adapter.kind == kind and host in adapter.hosts:
            return adapter
    for adapter in _REGISTRY:
        if adapter.kind == kind and not adapter.hosts:
            return adapter
    return None


def parse(ctx: ParseContext) -> list[RawItem]:
    adapter = adapter_for(ctx.source.kind, ctx.source.host)
    if adapter is None:
        raise ParseError(f"no adapter for {ctx.source.kind} sources on {ctx.source.host}")
    return adapter.parse(ctx)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


class _HTMLTables(HTMLParser):
    """Collects every <table> as rows of cell text (nested tables kept separate)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self._skip += 1
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None and self._stack:
            self._stack[-1][-1].append(clean_text("".join(self._cell)))
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append([r for r in self._stack.pop() if r])

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def html_tables(text: str) -> list[list[list[str]]]:
    parser = _HTMLTables()
    parser.feed(text)
    parser.close()
    return parser.tables


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(fragment: str) -> str:
    parser = _HTMLText()
    parser.feed(fragment or "")
    parser.close()
    return clean_text("".join(parser.parts))


# ---------------------------------------------------------------------------
# Schedules (tables of versions and dates)
# ---------------------------------------------------------------------------

_VERSION_CELL_RE = re.compile(r"^v?(\d{6}|\d{1,3}(?:\.\d+)?)$", re.IGNORECASE)


def _find_columns(header: list[str]) -> dict[str, int] | None:
    cols: dict[str, int] = {}
    for i, cell in enumerate(h.lower() for h in header):
        if "version" in cell and "version" not in cols:
            cols["version"] = i
        elif "release" in cell and "release" not in cols:
            cols["release"] = i
        elif "deprecat" in cell and "deprecation" not in cols:
            cols["deprecation"] = i
        elif any(k in cell for k in ("sunset", "expir", "end of life", "eol")) and "sunset" not in cols:
            cols["sunset"] = i
    if "version" in cols and ("sunset" in cols or "deprecation" in cols):
        return cols
    return None


def schedule_items(ctx: ParseContext, table: list[list[str]]) -> list[RawItem]:
    """Interpret one table as a version schedule. Returns [] if it is not one."""
    if not table:
        return []
    cols = _find_columns(table[0])
    if cols is None:
        return []
    name = ctx.partner.display_name
    items: list[RawItem] = []
    for row in table[1:]:
        cell = lambda key: row[cols[key]].strip() if key in cols and cols[key] < len(row) else ""  # noqa: E731
        m = _VERSION_CELL_RE.match(cell("version").rstrip("*").strip())
        if not m:
            continue  # section headings such as "Upcoming versions"
        version = canonical_version(m.group(1))
        released = cell("release")
        for kind, raw_date in (("sunset", cell("sunset")), ("deprecation", cell("deprecation"))):
            if not raw_date or parse_date(raw_date) is None:
                continue  # "TBD", blank, or no such column
            body = f"{name} {version} {kind} date: {raw_date}."
            if released:
                body += f" Released {released}."
            items.append(RawItem(
                partner=ctx.partner.id, source_kind=ctx.source.kind, source_url=ctx.source.url,
                title=f"{name} {version} {kind}", body=body, effective_at=raw_date, kind=kind,
                versions=(version,),
            ))
    return items


def _schedule_from_tables(ctx: ParseContext, tables: list[list[list[str]]]) -> list[RawItem]:
    items = [item for table in tables for item in schedule_items(ctx, table)]
    if not items:
        raise ParseError("no version schedule found (the page layout may have changed)")
    return items


def _looks_like_html(text: str) -> bool:
    return text.lstrip()[:1] == "<"


def parse_csv_schedule(ctx: ParseContext) -> list[RawItem]:
    text = ctx.result.text()
    if _looks_like_html(text):
        raise ParseError("expected CSV but received an HTML page")
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]
    return _schedule_from_tables(ctx, [rows])


def parse_html_schedule(ctx: ParseContext) -> list[RawItem]:
    return _schedule_from_tables(ctx, html_tables(ctx.result.text()))


# Some documentation sites render client-side: the tables live in a JSON payload
# inside <script type="application/json"> as a component tree of
# {"type": "...Table" | "...Tr" | "...Td" | "...Th", "children": [...]} nodes.
_JSON_SCRIPT_RE = re.compile(r"<script[^>]*type=\"application/json\"[^>]*>(.*?)</script>", re.S | re.I)


def _component_tables(node: Any, out: list[Any]) -> None:
    if isinstance(node, dict):
        if str(node.get("type", "")).endswith("Table"):
            out.append(node)
            return
        for v in node.values():
            _component_tables(v, out)
    elif isinstance(node, list):
        for v in node:
            _component_tables(v, out)
    elif isinstance(node, str) and node[:1] in "{[" and "Table" in node:
        try:
            _component_tables(json.loads(node), out)
        except ValueError:
            pass


def _component_text(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return _component_text(node.get("children", []))
    if isinstance(node, list):
        return " ".join(t for t in (_component_text(x) for x in node) if t)
    return ""


def _component_rows(node: Any) -> Iterator[list[str]]:
    if isinstance(node, dict):
        if str(node.get("type", "")).endswith("Tr"):
            yield [clean_text(_component_text(c)) for c in node.get("children", []) if isinstance(c, dict)]
            return
        yield from _component_rows(node.get("children", []))
    elif isinstance(node, list):
        for x in node:
            yield from _component_rows(x)


def component_tables(text: str) -> list[list[list[str]]]:
    tables: list[Any] = []
    for blob in _JSON_SCRIPT_RE.findall(text):
        if "Table" not in blob:
            continue
        try:
            _component_tables(json.loads(blob), tables)
        except ValueError:
            continue
    return [list(_component_rows(t)) for t in tables]


def parse_rendered_schedule(ctx: ParseContext) -> list[RawItem]:
    """Server-rendered tables if present, else tables embedded as component JSON."""
    text = ctx.result.text()
    return _schedule_from_tables(ctx, html_tables(text) + component_tables(text))


# ---------------------------------------------------------------------------
# Feeds (RSS 2.0 and Atom)
# ---------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def _child_text(el: ET.Element, *names: str) -> str:
    for name in names:
        child = el.find(name)
        if child is not None and (child.text or "").strip():
            return child.text or ""
    return ""


def parse_feed(ctx: ParseContext) -> list[RawItem]:
    body = ctx.result.body
    # Refuse DTD entity declarations outright (entity-expansion attacks).
    if b"<!ENTITY" in body:
        raise ParseError("feed declares XML entities; refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ParseError(f"feed is not valid XML: {e}") from None

    items: list[RawItem] = []
    if root.tag == f"{_ATOM}feed":
        for entry in root.findall(f"{_ATOM}entry")[:MAX_FEED_ENTRIES]:
            link = ""
            for el in entry.findall(f"{_ATOM}link"):
                if el.get("rel", "alternate") == "alternate" and el.get("href"):
                    link = el.get("href", "")
                    break
            items.append(_feed_item(
                ctx,
                title=_child_text(entry, f"{_ATOM}title"),
                summary=_child_text(entry, f"{_ATOM}summary", f"{_ATOM}content"),
                published=_child_text(entry, f"{_ATOM}published", f"{_ATOM}updated"),
                link=link,
            ))
    elif root.tag == "rss":
        for entry in root.findall("./channel/item")[:MAX_FEED_ENTRIES]:
            items.append(_feed_item(
                ctx,
                title=_child_text(entry, "title"),
                summary=_child_text(entry, "description"),
                published=_child_text(entry, "pubDate"),
                link=_child_text(entry, "link").strip(),
            ))
    else:
        raise ParseError(f"not an RSS or Atom feed (root element {root.tag!r})")
    items = [i for i in items if i.title.strip()]
    if not items:
        raise ParseError("feed has no entries")
    return items


def _feed_item(ctx: ParseContext, *, title: str, summary: str, published: str, link: str) -> RawItem:
    # Only keep permalinks on https; otherwise point at the feed itself.
    url = link if link.startswith("https://") else None
    return RawItem(
        partner=ctx.partner.id, source_kind=ctx.source.kind, source_url=ctx.source.url,
        title=html_to_text(title), body=html_to_text(summary), published_at=published or None, url=url,
        title_only=True,  # blog bodies mention versions and "sunset" in passing
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

register(Adapter("csv-schedule", "csv_schedule", parse_csv_schedule))
register(Adapter("html-schedule", "sunset_schedule", parse_html_schedule))
# This host renders its version table client-side, as component JSON.
register(Adapter("rendered-schedule", "sunset_schedule", parse_rendered_schedule, hosts=("developers.facebook.com",)))
register(Adapter("feed", "feed", parse_feed))
