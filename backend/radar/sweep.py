"""One partner sweep: fetch every source, parse, normalize, dedupe into the store.

Deterministic end to end: no model calls. Each source's outcome is recorded in
the store's source health; two consecutive failures of a source mark its partner
stale (see ``change_store.STALE_AFTER_FAILURES``). A source with no adapter is
``unsupported``, which is reported but never counts as a failure.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .adapters import ParseContext, ParseError, adapter_for
from .change_store import ChangeStore
from .fetch import Fetcher, FetchError
from .manifest import Manifest
from .normalize import normalize

log = logging.getLogger(__name__)

#: Schedule rows for versions that sunset long ago are history, not news.
MAX_EFFECTIVE_AGE_DAYS = 180
#: Undated announcements (feed posts) older than this are not ingested.
MAX_PUBLISHED_AGE_DAYS = 365

FetcherFactory = Callable[[Iterable[str]], Fetcher]


@dataclass
class SourceReport:
    partner: str
    url: str
    kind: str
    status: str  # ok | failing | unsupported
    adapter: str | None = None
    items: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"partner": self.partner, "url": self.url, "kind": self.kind, "status": self.status,
                "adapter": self.adapter, "items": self.items, "error": self.error}


@dataclass
class SweepReport:
    started_at: str
    finished_at: str = ""
    sources: list[SourceReport] = field(default_factory=list)
    outcomes: Counter = field(default_factory=Counter)
    changed: list[dict[str, str]] = field(default_factory=list)  # new or updated changes
    skipped_old: int = 0
    stale_partners: list[str] = field(default_factory=list)
    newly_stale: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "startedAt": self.started_at, "finishedAt": self.finished_at,
            "sources": [s.to_dict() for s in self.sources],
            "outcomes": {k: self.outcomes.get(k, 0) for k in ("new", "merged", "updated", "unchanged")},
            "changed": self.changed, "skippedOld": self.skipped_old,
            "stalePartners": self.stale_partners, "newlyStale": self.newly_stale, "recovered": self.recovered,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Sweeper:
    def __init__(
        self,
        store: ChangeStore,
        *,
        fetcher_factory: FetcherFactory = Fetcher,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.fetcher_factory = fetcher_factory
        self.now = now

    async def run(self, manifest: Manifest, partner_ids: Iterable[str] | None = None) -> SweepReport:
        wanted = set(partner_ids) if partner_ids is not None else set(manifest.partners)
        report = SweepReport(started_at=self._stamp())
        self.store.prune_sources({(p.id, src.url) for p in manifest.partners.values() for src in p.sources})
        stale_before = set(self.store.stale_partners())
        today = self.now().astimezone(timezone.utc).date()

        fetcher = self.fetcher_factory(manifest.allowed_hosts)
        try:
            for partner in manifest.partners.values():
                if partner.id not in wanted:
                    continue
                for source in partner.sources:
                    report.sources.append(await self._sweep_source(fetcher, partner, source, today, report))
        finally:
            await fetcher.close()

        stale_after = self.store.stale_partners()
        report.stale_partners = sorted(stale_after)
        report.newly_stale = sorted(set(stale_after) - stale_before)
        report.recovered = sorted(stale_before - set(stale_after))
        report.finished_at = self._stamp()
        return report

    async def _sweep_source(self, fetcher, partner, source, today: date, report: SweepReport) -> SourceReport:
        adapter = adapter_for(source.kind, source.host)
        if adapter is None:
            self.store.record_source(partner.id, source.url, source.kind, status="unsupported")
            return SourceReport(partner.id, source.url, source.kind, "unsupported",
                                error=f"no adapter yet for {source.kind} sources")
        try:
            result = await fetcher.fetch(source.url)
            raw_items = adapter.parse(ParseContext(partner, source, result))
        except (FetchError, ParseError) as e:
            return self._fail(partner.id, source, adapter.name, str(e))
        except Exception as e:  # an adapter bug must not abort the whole sweep
            log.exception("adapter %s crashed on %s", adapter.name, source.url)
            return self._fail(partner.id, source, adapter.name, f"internal error: {e.__class__.__name__}")

        ingested = 0
        for raw in raw_items:
            item = normalize(raw)
            if item is None:
                continue
            if self._too_old(item.effective_at, item.published_at, today):
                report.skipped_old += 1
                continue
            result = self.store.upsert(item)
            report.outcomes[result.outcome] += 1
            ingested += 1
            if result.outcome in ("new", "updated"):
                report.changed.append({"id": result.change.id, "outcome": result.outcome})
        self.store.record_source(partner.id, source.url, source.kind, status="ok", items=ingested)
        return SourceReport(partner.id, source.url, source.kind, "ok", adapter.name, ingested)

    def _fail(self, partner_id: str, source, adapter: str, error: str) -> SourceReport:
        log.warning("source %s failed: %s", source.url, error)
        self.store.record_source(partner_id, source.url, source.kind, status="failing", error=error)
        return SourceReport(partner_id, source.url, source.kind, "failing", adapter, error=error)

    @staticmethod
    def _too_old(effective_at: str | None, published_at: str | None, today: date) -> bool:
        if effective_at:
            return date.fromisoformat(effective_at) < today - timedelta(days=MAX_EFFECTIVE_AGE_DAYS)
        if published_at:
            return date.fromisoformat(published_at) < today - timedelta(days=MAX_PUBLISHED_AGE_DAYS)
        return False

    def _stamp(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat(timespec="seconds")
