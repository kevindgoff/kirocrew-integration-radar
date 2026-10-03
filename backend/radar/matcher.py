"""Impact matching: which connectors a change affects, and how sure we are.

Deterministic: no model calls. A change can only match connectors of its own
partner. Each match carries a confidence and the reasons behind it.

Confidence:
  exact     the change names a field the connector lists, or sunsets/deprecates
            the version the connector pins;
  likely    any other change to the pinned version, or it names a service,
            endpoint or distinctive resource the connector uses;
  possible  a plain-word mention without context (e.g. "clicks") in a change that
            is not purely additive, or partner-wide breaking/deprecation news
            that names no version.

Plain words ("clicks", "spend", "campaign") are everyday English in partner
posts, so they only count as an exact field mention in a field context ("the
`spend` field", "metric clicks", a quoted name); otherwise they are possible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .change_store import Change
from .manifest import Connector, Manifest
from .normalize import canonical_version

CONFIDENCE_RANK = {"exact": 3, "likely": 2, "possible": 1}
CRITICALITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}

# Deadline bonus added to the impact score: closer deadlines rank higher.
URGENT_DAYS = 30
DEADLINE_BONUS = ((URGENT_DAYS, 6), (90, 3), (180, 1))

_FIELD_CONTEXT = r"(?:fields?|metrics?|parameters?|params?|columns?|attributes?|propert(?:y|ies)|dimensions?|segments?)"
_MIN_PLAIN_WORD = 4  # "ad", "id", "cpm" are too ambiguous to match on as plain words


@dataclass(frozen=True)
class Match:
    connector: str
    confidence: str
    reasons: tuple[str, ...]
    action: str  # "fix" or "migrate or retire"
    criticality: str

    def to_dict(self) -> dict[str, Any]:
        return {"connector": self.connector, "confidence": self.confidence, "reasons": list(self.reasons),
                "action": self.action, "criticality": self.criticality}


@dataclass
class _Signals:
    best: int = 0
    reasons: list[str] = field(default_factory=list)

    def add(self, confidence: str, reason: str) -> None:
        self.best = max(self.best, CONFIDENCE_RANK[confidence])
        if reason not in self.reasons:
            self.reasons.append(reason)


def _is_identifier(term: str) -> bool:
    """Distinctive API names: dotted, snake_case, camelCase, or with digits."""
    return bool(re.search(r"[._/{}]|\d|[a-z][A-Z]", term))


def _mention_re(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w.])" + re.escape(term) + r"(?!\w|\.\w)", re.IGNORECASE)


def _mentions(text: str, term: str) -> bool:
    return bool(term) and _mention_re(term).search(text) is not None


def _in_field_context(text: str, term: str) -> bool:
    t = re.escape(term)
    patterns = (
        rf"[`'\"]{t}[`'\"]",  # quoted
        rf"\b{_FIELD_CONTEXT}\s+(?:named\s+|called\s+)?{t}\b",  # "field spend", "metric clicks"
        rf"\b{t}\s+{_FIELD_CONTEXT}\b",  # "spend field"
    )
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _endpoint_terms(endpoint: str) -> list[str]:
    """Static path segments of an endpoint: '/{ad-account-id}/insights' -> ['insights']."""
    return [seg for seg in endpoint.strip("/").split("/") if seg and not seg.startswith("{")]


def match_change(change: Change, connector: Connector) -> Match | None:
    if change.partner != connector.partner:
        return None
    text = f"{change.title}\n{change.body}"
    s = _Signals()
    # Plain-word mentions only matter when something is going away or breaking;
    # in purely additive news ("Announcing v25 ... campaign ...") they are noise.
    plain_words_count = change.kind != "additive"

    # Versions.
    pinned = canonical_version(connector.api.version)
    if pinned in change.versions:
        if change.kind in ("sunset", "deprecation"):
            s.add("exact", f"{change.kind} of pinned version {pinned}")
        else:
            s.add("likely", f"{change.kind} change in pinned version {pinned}")

    # Surfaces: fields, services/endpoints, resources.
    for surface in connector.surfaces:
        for f in surface.fields:
            if not _mentions(text, f):
                continue
            if _is_identifier(f) or _in_field_context(text, f):
                s.add("exact", f"names field {f}")
            elif plain_words_count and len(f) >= _MIN_PLAIN_WORD:
                s.add("possible", f"mentions '{f}' (not clearly as a field)")
        if surface.kind == "service":
            parts = [surface.name, *surface.name.split(".")]
            if any(_mentions(text, p) for p in parts if len(p) >= _MIN_PLAIN_WORD):
                s.add("likely", f"names service {surface.name}")
        else:
            for seg in _endpoint_terms(surface.name):
                if re.search(rf"/{re.escape(seg)}\b|\b{re.escape(seg)}\s+(?:endpoint|edge|api)\b", text, re.IGNORECASE):
                    s.add("likely", f"names endpoint {surface.name}")
                elif plain_words_count and len(seg) >= _MIN_PLAIN_WORD and _mentions(text, seg):
                    s.add("possible", f"mentions '{seg}' from endpoint {surface.name}")
        for r in surface.resources:
            if not _mentions(text, r):
                continue
            if _is_identifier(r):
                s.add("likely", f"names resource {r}")
            elif plain_words_count and len(r) >= _MIN_PLAIN_WORD:
                s.add("possible", f"mentions '{r}'")

    # Partner-wide news with no version attached may apply to every version.
    if not change.versions and change.kind in ("breaking", "deprecation", "sunset") and not s.best:
        s.add("possible", f"partner-wide {change.kind} with no version stated")

    if not s.best:
        return None
    confidence = next(k for k, v in CONFIDENCE_RANK.items() if v == s.best)
    return Match(connector.id, confidence, tuple(s.reasons), connector.action, connector.criticality)


def match_all(change: Change, connectors: Iterable[Connector]) -> list[Match]:
    matches = [m for m in (match_change(change, c) for c in connectors) if m is not None]
    return sorted(matches, key=lambda m: (-CONFIDENCE_RANK[m.confidence], -CRITICALITY_WEIGHT[m.criticality], m.connector))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedChange:
    change: Change
    matches: tuple[Match, ...]
    impact: str | None  # best confidence across matches, None if unmatched
    days_left: int | None
    urgent: bool
    score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.change.to_dict(),
            "matches": [m.to_dict() for m in self.matches],
            "impact": self.impact,
            "daysLeft": self.days_left,
            "urgent": self.urgent,
            "score": self.score,
        }


def rank(changes: Iterable[Change], manifest: Manifest | None, today: date) -> list[RankedChange]:
    """Rank by impact (confidence x connector criticality) plus a deadline bonus.

    Unmatched changes score 0 and sink to the bottom; ties go to the nearest
    deadline, then the most recently seen.
    """
    connectors = manifest.connectors if manifest is not None else ()
    ranked: list[RankedChange] = []
    for change in changes:
        matches = tuple(match_all(change, connectors))
        days_left = None
        if change.effective_at:
            days_left = (date.fromisoformat(change.effective_at) - today).days
        score = 0
        if matches:
            score = max(CONFIDENCE_RANK[m.confidence] * CRITICALITY_WEIGHT[m.criticality] for m in matches)
            if days_left is not None:
                score += next((bonus for limit, bonus in DEADLINE_BONUS if days_left <= limit), 0)
        urgent = bool(matches) and days_left is not None and days_left < URGENT_DAYS
        ranked.append(RankedChange(change, matches, matches[0].confidence if matches else None, days_left, urgent, score))

    def key(r: RankedChange):
        return (-r.score, r.change.effective_at is None, r.change.effective_at or "", _neg(r.change.last_seen), r.change.id)

    return sorted(ranked, key=key)


def _neg(stamp: str) -> tuple[int, ...]:
    # Sort ISO timestamps descending inside an ascending key.
    return tuple(-ord(c) for c in stamp)
