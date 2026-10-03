"""Turn raw fetched items into normalized change records. Deterministic: no model calls.

Fetchers (one adapter per source kind and host) emit :class:`RawItem`s. The
normalizer cleans text, classifies the change kind, parses dates, extracts API
versions and computes two identities:

* ``content_hash`` changes whenever the announcement's content changes.
* the match keys (``strong_key`` and ``title_key``) decide whether two items are
  the same announcement, so one announcement seen on two sources is one record.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

KINDS = ("deprecation", "sunset", "breaking", "additive")
# Partner text is untrusted; cap what one record can hold.
MAX_TITLE_CHARS = 500
MAX_BODY_CHARS = 8000
# Sources whose items are end-of-life dates unless the adapter says otherwise.
SCHEDULE_SOURCE_KINDS = ("sunset_schedule", "csv_schedule")

_SUNSET_RE = re.compile(
    r"\bsunset|\bturned off\b|\bshut ?down\b|\bend[- ]of[- ]life\b|\beol\b|"
    r"\bno longer (?:be )?available\b|\bwill stop working\b|\bdecommission",
    re.IGNORECASE,
)
_BREAKING_RE = re.compile(
    r"\bbreaking\b|\bwill be removed\b|\bremoved\b|\bremoval\b|\bno longer supported\b|"
    r"\bincompatible\b|\bnow required\b|\bout[- ]of[- ]cycle\b",
    re.IGNORECASE,
)
_DEPRECATION_RE = re.compile(r"\bdeprecat", re.IGNORECASE)

# v21, v24.0, v202508. Not v1.2.3-style client versions, and not path segments like /v1/.
_VERSION_RE = re.compile(r"(?<![\w/.])v(\d{6}|\d{1,3}(?:\.\d+)?)(?![\w/]|\.\d)", re.IGNORECASE)
# "migrate to v21", "replaced by v22": the version named is the target, not the one affected.
_TARGET_VERSION_RE = re.compile(
    r"\b(?:(?:migrate|upgrade|move|switch|update|transition)\s+to|in\s+favou?r\s+of|replaced\s+by|"
    r"successor(?:\s+is)?)\s+(?:the\s+)?(?:api\s+)?(?:version\s+)?v(\d{6}|\d{1,3}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%Y%m%d",
)
_MONTH_FORMATS = ("%B %Y", "%b %Y", "%Y-%m")

DateLike = str | date | datetime | None


@dataclass(frozen=True)
class RawItem:
    """One announcement as an adapter extracted it. All text is untrusted partner content."""

    partner: str
    source_kind: str
    source_url: str
    title: str
    body: str = ""
    published_at: DateLike = None
    effective_at: DateLike = None
    kind: str | None = None  # adapter hint, e.g. a CSV column that says "sunset"
    versions: tuple[str, ...] = ()  # adapter-provided versions; else extracted from text
    url: str | None = None  # item permalink, if the source has one (feeds, anchors)
    # Classify kind and extract versions from the title alone. For sources whose
    # bodies are long prose (blog posts) that mention many versions in passing.
    title_only: bool = False


@dataclass(frozen=True)
class NormalizedChange:
    partner: str
    title: str
    kind: str
    published_at: str | None  # YYYY-MM-DD
    effective_at: str | None  # YYYY-MM-DD
    source_url: str
    source_kind: str
    content_hash: str
    versions: tuple[str, ...] = ()
    body: str = ""
    strong_key: str | None = None
    title_key: str = field(default="")
    effective_precision: str = "day"  # "day", or "month" for "October 2026"


def clean_text(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def canonical_version(version: str) -> str:
    """Canonical form for comparing versions: 'V24.0' -> 'v24', '202508' -> 'v202508'."""
    v = version.strip().lower()
    if not v.startswith("v"):
        v = "v" + v
    if re.fullmatch(r"v\d+\.0+", v):
        v = v.split(".", 1)[0]
    return v


def extract_versions(*texts: str) -> tuple[str, ...]:
    """Affected versions in the first text that mentions any, canonicalized and sorted.

    Migration targets ("migrate to v21") are left out unless they are the only
    versions mentioned.
    """
    for text in texts:
        found = {canonical_version("v" + m.group(1)) for m in _VERSION_RE.finditer(text or "")}
        if found:
            targets = {canonical_version("v" + m.group(1)) for m in _TARGET_VERSION_RE.finditer(text)}
            affected = (found - targets) or found
            return tuple(sorted(affected, key=_version_sort_key))
    return ()


def _version_sort_key(v: str) -> tuple[float, str]:
    try:
        return (float(v[1:]), v)
    except ValueError:
        return (float("inf"), v)


def parse_date(value: DateLike) -> str | None:
    """Parse common partner date formats to YYYY-MM-DD. Unparseable values return None.

    Month-only dates ("October 2026 (tentative)") resolve to the 1st of the month,
    the earliest day they could mean; use :func:`parse_date_precision` to tell.
    """
    return parse_date_precision(value)[0]


def parse_date_precision(value: DateLike) -> tuple[str | None, str]:
    """Like :func:`parse_date`, also returning the precision: ``"day"`` or ``"month"``."""
    if value is None:
        return None, "day"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date().isoformat(), "day"
    if isinstance(value, date):
        return value.isoformat(), "day"
    # Drop qualifiers such as "(tentative)" and footnote markers such as "*".
    text = clean_text(re.sub(r"\([^)]*\)|\*", " ", str(value)))
    if not text:
        return None, "day"
    # ISO 8601 date-times ("2025-08-01T10:00:00Z") and plain ISO dates.
    try:
        return parse_date_precision(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    # RFC 822 dates from RSS feeds ("Fri, 01 Aug 2025 10:00:00 GMT").
    try:
        return parse_date_precision(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass
    stripped = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text).replace("Sept ", "Sep ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date().isoformat(), "day"
        except ValueError:
            continue
    for fmt in _MONTH_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date().isoformat(), "month"
        except ValueError:
            continue
    return None, "day"


def classify(title: str, body: str, source_kind: str, hint: str | None = None) -> str:
    """Pick a change kind. An adapter hint wins; then the title; then the body; then the source."""
    if hint:
        h = hint.strip().lower()
        if h in KINDS:
            return h
    for text in (title, body):
        if _SUNSET_RE.search(text):
            return "sunset"
        if _BREAKING_RE.search(text):
            return "breaking"
        if _DEPRECATION_RE.search(text):
            return "deprecation"
    if source_kind in SCHEDULE_SOURCE_KINDS:
        return "sunset"
    return "additive"


def normalize_title(title: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", title.lower())).strip()


def _hash(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def normalize(item: RawItem) -> NormalizedChange | None:
    """Normalize one raw item. Items without a title are dropped (None)."""
    title = clean_text(item.title)
    if not title:
        return None
    body = clean_text(item.body)
    signal_body = "" if item.title_only else body
    kind = classify(title, signal_body, item.source_kind, item.kind)
    if item.versions:
        versions = tuple(sorted({canonical_version(v) for v in item.versions if v.strip()}, key=_version_sort_key))
    else:
        versions = extract_versions(title, signal_body)
    published_at = parse_date(item.published_at)
    effective_at, precision = parse_date_precision(item.effective_at)
    title_norm = normalize_title(title)
    # Only an exact (day) date is precise enough to identify an announcement by itself.
    strong_key = (
        f"{item.partner}|{kind}|{','.join(versions)}|{effective_at}"
        if versions and effective_at and precision == "day" else None
    )
    return NormalizedChange(
        partner=item.partner,
        title=title[:MAX_TITLE_CHARS],
        kind=kind,
        published_at=published_at,
        effective_at=effective_at,
        source_url=item.url or item.source_url,
        source_kind=item.source_kind,
        content_hash=_hash(item.partner, kind, title_norm, body, effective_at, precision, versions),
        versions=versions,
        body=body[:MAX_BODY_CHARS],
        strong_key=strong_key,
        title_key=f"{item.partner}|{kind}|{title_norm}",
        effective_precision=precision,
    )
