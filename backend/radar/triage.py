"""Triage helpers: prior-dismissal matching, lesson text, and Markdown export.

Deterministic. A change that resembles one the team already dismissed is shown
collapsed with the matching lesson, never hidden: same partner and kind, and
enough overlap in the title's words once version numbers and dates are ignored
(so a dismissed "v22 sunset" also covers "v23 sunset").
"""

from __future__ import annotations

import re
from typing import Iterable

from .change_store import Change
from .matcher import RankedChange
from .normalize import normalize_title

#: Title-word overlap (Jaccard) at or above which a dismissal is considered to match.
SIMILARITY_THRESHOLD = 0.6
MAX_LESSON_CHARS = 500  # Crew's lesson rule limit

_STOPWORDS = {"the", "and", "for", "with", "from", "into", "will", "are", "was", "this", "that", "api", "new",
              "now", "its", "our", "your", "has", "have", "been", "all", "any", "of", "to", "in", "on", "a", "an",
              "is", "be", "by", "at", "or"}
_NOISE_RE = re.compile(r"^(v?\d[\d.]*|\d+(st|nd|rd|th)?|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
                       r"january|february|march|april|june|july|august|september|october|november|december)$")


def title_signature(title: str) -> frozenset[str]:
    words = normalize_title(title).split()
    return frozenset(w for w in words if len(w) >= 3 and w not in _STOPWORDS and not _NOISE_RE.match(w))


def similarity(a: str, b: str) -> float:
    sa, sb = title_signature(a), title_signature(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_prior_dismissal(change: Change, dismissed: Iterable[Change]) -> Change | None:
    best: tuple[float, str, Change] | None = None
    for d in dismissed:
        if d.id == change.id or d.partner != change.partner or d.kind != change.kind:
            continue
        score = similarity(change.title, d.title)
        if score >= SIMILARITY_THRESHOLD and (best is None or (score, d.triaged_at or "") > best[:2]):
            best = (score, d.triaged_at or "", d)
    return best[2] if best else None


def lesson_rule(change: Change, partner_name: str, reason: str) -> str:
    rule = (f"Integration Radar: a {change.kind} notice from {partner_name} (\"{change.title}\") "
            f"was dismissed as not needing work. Reason: {reason.strip()}")
    return rule if len(rule) <= MAX_LESSON_CHARS else rule[: MAX_LESSON_CHARS - 1] + "…"


def to_markdown(ranked: RankedChange, partner_name: str) -> str:
    """A self-contained Markdown write-up of a change, ready to paste into any tracker."""
    c = ranked.change
    lines = [f"## {c.title}", ""]
    lines.append(f"- **Partner:** {partner_name}")
    lines.append(f"- **Kind:** {c.kind}")
    if c.versions:
        lines.append(f"- **Versions:** {', '.join(c.versions)}")
    if c.effective_at:
        when = c.effective_at[:7] + " (month only)" if c.effective_precision == "month" else c.effective_at
        if ranked.days_left is not None:
            rel = f"{-ranked.days_left} days ago" if ranked.days_left < 0 else f"in {ranked.days_left} days"
            when += f", {rel}"
        lines.append(f"- **Effective:** {when}")
    if c.published_at:
        lines.append(f"- **Announced:** {c.published_at}")
    lines.append("")
    if ranked.matches:
        lines.append("### Affected connectors")
        lines.append("")
        for m in ranked.matches:
            lines.append(f"- `{m.connector}` ({m.criticality} criticality): **{m.confidence}**, "
                         f"action: {m.action}. {'; '.join(m.reasons).capitalize()}.")
        lines.append("")
    if c.body:
        lines.append("### What the partner says")
        lines.append("")
        excerpt = c.body if len(c.body) <= 1500 else c.body[:1500] + "…"
        lines.extend(f"> {line}" for line in excerpt.splitlines() or [excerpt])
        lines.append("")
    lines.append("### Sources")
    lines.append("")
    lines.extend(f"- {url}" for url in (c.sources or (c.source_url,)))
    return "\n".join(lines).rstrip() + "\n"
