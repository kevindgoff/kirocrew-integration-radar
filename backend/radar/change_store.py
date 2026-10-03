"""SQLite store for change records, with cross-source dedupe and triage state.

One announcement seen on several sources is one ``changes`` row; every source
that reported it is a ``change_sources`` row. :meth:`ChangeStore.upsert` returns
what happened (``new``, ``merged``, ``updated`` or ``unchanged``) so callers can
alert only on a state change, never per sweep.

Dedupe order for an incoming item:
  1. strong key: same partner, kind, versions and effective date;
  2. same source, same title: the source re-reporting it (it may move the date);
  3. title key: same partner, kind and normalized title, provided versions match
     and the effective dates do not contradict each other;
  4. partial: same partner, kind and versions where one side has no effective
     date yet (e.g. a release note and the sunset page for the same version).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .normalize import NormalizedChange

STATUSES = ("new", "accepted", "dismissed", "snoozed")
OUTCOMES = ("new", "merged", "updated", "unchanged")

# Each entry migrates the schema from version i to i+1 (PRAGMA user_version).
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE changes (
        id            TEXT PRIMARY KEY,
        partner       TEXT NOT NULL,
        kind          TEXT NOT NULL,
        title         TEXT NOT NULL,
        body          TEXT NOT NULL DEFAULT '',
        versions      TEXT NOT NULL DEFAULT '[]',
        published_at  TEXT,
        effective_at  TEXT,
        source_url    TEXT NOT NULL,
        source_kind   TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        strong_key    TEXT,
        title_key     TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'new',
        status_reason TEXT,
        snoozed_until TEXT,
        triaged_at    TEXT,
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    );
    CREATE INDEX changes_strong_key ON changes(strong_key);
    CREATE INDEX changes_title_key ON changes(title_key);
    CREATE INDEX changes_partner_kind ON changes(partner, kind);
    CREATE TABLE change_sources (
        change_id    TEXT NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
        source_url   TEXT NOT NULL,
        source_kind  TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        first_seen   TEXT NOT NULL,
        last_seen    TEXT NOT NULL,
        PRIMARY KEY (change_id, source_url)
    );
    """,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Change:
    id: str
    partner: str
    kind: str
    title: str
    body: str
    versions: tuple[str, ...]
    published_at: str | None
    effective_at: str | None
    source_url: str
    source_kind: str
    content_hash: str
    status: str
    status_reason: str | None
    snoozed_until: str | None
    triaged_at: str | None
    first_seen: str
    last_seen: str
    updated_at: str
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "partner": self.partner,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "versions": list(self.versions),
            "publishedAt": self.published_at,
            "effectiveAt": self.effective_at,
            "sourceUrl": self.source_url,
            "sourceKind": self.source_kind,
            "sources": list(self.sources),
            "contentHash": self.content_hash,
            "status": self.status,
            "statusReason": self.status_reason,
            "snoozedUntil": self.snoozed_until,
            "triagedAt": self.triaged_at,
            "firstSeen": self.first_seen,
            "lastSeen": self.last_seen,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class UpsertResult:
    change: Change
    outcome: str  # one of OUTCOMES


class ChangeStore:
    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = _utcnow) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._now = now
        self._migrate()

    def close(self) -> None:
        self._db.close()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(MIGRATIONS)):
            with self._db:
                self._db.executescript(MIGRATIONS[i])
                self._db.execute(f"PRAGMA user_version = {i + 1}")

    def _stamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")

    def _today(self) -> str:
        return self._now().astimezone(timezone.utc).date().isoformat()

    # -- ingest -------------------------------------------------------------

    def upsert(self, item: NormalizedChange) -> UpsertResult:
        with self._db:
            row = self._find(item)
            if row is None:
                return UpsertResult(self._insert(item), "new")
            return self._merge(row, item)

    def _find(self, item: NormalizedChange) -> sqlite3.Row | None:
        if item.strong_key:
            row = self._db.execute(
                "SELECT * FROM changes WHERE strong_key = ? ORDER BY first_seen LIMIT 1", (item.strong_key,)
            ).fetchone()
            if row:
                return row
        # The same source re-reporting the same title is the same announcement, even
        # if it moved the date.
        row = self._db.execute(
            "SELECT c.* FROM changes c JOIN change_sources s ON s.change_id = c.id "
            "WHERE c.title_key = ? AND s.source_url = ? ORDER BY c.first_seen LIMIT 1",
            (item.title_key, item.source_url),
        ).fetchone()
        if row:
            return row
        # A matching title from another source must not contradict versions or date.
        row = self._db.execute(
            "SELECT * FROM changes WHERE title_key = ? AND versions = ? "
            "AND (effective_at IS NULL OR ? IS NULL OR effective_at = ?) ORDER BY first_seen LIMIT 1",
            (item.title_key, json.dumps(list(item.versions)), item.effective_at, item.effective_at),
        ).fetchone()
        if row:
            return row
        if item.versions:
            # A dated item may only absorb an undated record (and vice versa), so two
            # different dates for the same versions stay two records.
            undated_only = "AND effective_at IS NULL" if item.effective_at else ""
            return self._db.execute(
                f"SELECT * FROM changes WHERE partner = ? AND kind = ? AND versions = ? {undated_only} "
                "ORDER BY first_seen LIMIT 1",
                (item.partner, item.kind, json.dumps(list(item.versions))),
            ).fetchone()
        return None

    def _insert(self, item: NormalizedChange) -> Change:
        now = self._stamp()
        change_id = uuid.uuid4().hex[:16]
        self._db.execute(
            """INSERT INTO changes (id, partner, kind, title, body, versions, published_at, effective_at,
                   source_url, source_kind, content_hash, strong_key, title_key,
                   first_seen, last_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (change_id, item.partner, item.kind, item.title, item.body, json.dumps(list(item.versions)),
             item.published_at, item.effective_at, item.source_url, item.source_kind, item.content_hash,
             item.strong_key, item.title_key, now, now, now),
        )
        self._add_source(change_id, item, now)
        return self._get(change_id)

    def _add_source(self, change_id: str, item: NormalizedChange, now: str) -> None:
        self._db.execute(
            "INSERT INTO change_sources (change_id, source_url, source_kind, content_hash, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (change_id, item.source_url, item.source_kind, item.content_hash, now, now),
        )

    def _merge(self, row: sqlite3.Row, item: NormalizedChange) -> UpsertResult:
        now = self._stamp()
        change_id = row["id"]
        source = self._db.execute(
            "SELECT content_hash FROM change_sources WHERE change_id = ? AND source_url = ?",
            (change_id, item.source_url),
        ).fetchone()
        is_primary = item.source_url == row["source_url"]

        updates: dict[str, Any] = {}
        # The effective date is filled in by whichever source has it; the primary
        # source may also move it (a partner pushing a sunset date back).
        if item.effective_at and (row["effective_at"] is None or (is_primary and item.effective_at != row["effective_at"])):
            updates["effective_at"] = item.effective_at
        if item.published_at and (row["published_at"] is None or item.published_at < row["published_at"]):
            updates["published_at"] = item.published_at
        if is_primary and item.content_hash != row["content_hash"]:
            updates.update(title=item.title, body=item.body, content_hash=item.content_hash)
        if "effective_at" in updates:
            versions = json.loads(row["versions"])
            updates["strong_key"] = (
                f"{row['partner']}|{row['kind']}|{','.join(versions)}|{updates['effective_at']}" if versions else None
            )

        if source is None:
            self._add_source(change_id, item, now)
            outcome = "merged"
        else:
            self._db.execute(
                "UPDATE change_sources SET content_hash = ?, last_seen = ? WHERE change_id = ? AND source_url = ?",
                (item.content_hash, now, change_id, item.source_url),
            )
            outcome = "updated" if updates else "unchanged"

        updates["last_seen"] = now
        if outcome != "unchanged":
            updates["updated_at"] = now
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._db.execute(f"UPDATE changes SET {assignments} WHERE id = ?", (*updates.values(), change_id))
        return UpsertResult(self._get(change_id), outcome)

    # -- reads --------------------------------------------------------------

    def _get(self, change_id: str) -> Change:
        change = self.get(change_id)
        assert change is not None
        return change

    def get(self, change_id: str) -> Change | None:
        row = self._db.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        return self._to_change(row) if row else None

    def list(self, *, status: str | None = None, partner: str | None = None) -> list[Change]:
        """All changes, with expired snoozes reported as 'new'. Ordering is left to the feed ranker."""
        rows = self._db.execute("SELECT * FROM changes ORDER BY first_seen, id").fetchall()
        out = [self._to_change(r) for r in rows]
        if partner is not None:
            out = [c for c in out if c.partner == partner]
        if status is not None:
            out = [c for c in out if c.status == status]
        return out

    def _to_change(self, row: sqlite3.Row) -> Change:
        sources = tuple(
            r["source_url"]
            for r in self._db.execute(
                "SELECT source_url FROM change_sources WHERE change_id = ? ORDER BY rowid",
                (row["id"],),
            )
        )
        status = row["status"]
        snoozed_until = row["snoozed_until"]
        if status == "snoozed" and snoozed_until and snoozed_until <= self._today():
            status, snoozed_until = "new", None
        return Change(
            id=row["id"], partner=row["partner"], kind=row["kind"], title=row["title"], body=row["body"],
            versions=tuple(json.loads(row["versions"])), published_at=row["published_at"],
            effective_at=row["effective_at"], source_url=row["source_url"], source_kind=row["source_kind"],
            content_hash=row["content_hash"], status=status, status_reason=row["status_reason"],
            snoozed_until=snoozed_until, triaged_at=row["triaged_at"], first_seen=row["first_seen"],
            last_seen=row["last_seen"], updated_at=row["updated_at"], sources=sources,
        )

    # -- triage -------------------------------------------------------------

    def accept(self, change_id: str) -> Change:
        return self._set_status(change_id, "accepted", None, None)

    def dismiss(self, change_id: str, reason: str) -> Change:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("dismissing a change requires a reason")
        return self._set_status(change_id, "dismissed", reason, None)

    def snooze(self, change_id: str, until: str | date) -> Change:
        until_s = until.isoformat() if isinstance(until, date) else str(until)
        try:
            date.fromisoformat(until_s)
        except ValueError:
            raise ValueError("snooze date must be YYYY-MM-DD") from None
        if until_s <= self._today():
            raise ValueError("snooze date must be in the future")
        return self._set_status(change_id, "snoozed", None, until_s)

    def reopen(self, change_id: str) -> Change:
        return self._set_status(change_id, "new", None, None)

    def _set_status(self, change_id: str, status: str, reason: str | None, until: str | None) -> Change:
        with self._db:
            cur = self._db.execute(
                "UPDATE changes SET status = ?, status_reason = ?, snoozed_until = ?, triaged_at = ? WHERE id = ?",
                (status, reason, until, self._stamp(), change_id),
            )
        if cur.rowcount == 0:
            raise KeyError(change_id)
        return self._get(change_id)
