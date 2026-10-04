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
  4. sunsets only: same partner and versions with non-contradicting dates (e.g. a
     release note and the sunset page for the same version).

Dates do not contradict when either is missing, they are equal, or one is only
month-precise ("May 2026") and the other falls in that month. A day-precise date
from any source sharpens a month-precise one.
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
    """
    ALTER TABLE changes ADD COLUMN effective_precision TEXT NOT NULL DEFAULT 'day';
    CREATE TABLE source_health (
        partner              TEXT NOT NULL,
        source_url           TEXT NOT NULL,
        source_kind          TEXT NOT NULL,
        status               TEXT NOT NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_attempt         TEXT,
        last_success         TEXT,
        last_error           TEXT,
        items_last_run       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (partner, source_url)
    );
    """,
    # Lessons from dismissals, queued until an agent saves them into Crew
    # (page apps and backends cannot write lessons directly).
    """
    CREATE TABLE lessons (
        change_id  TEXT PRIMARY KEY REFERENCES changes(id) ON DELETE CASCADE,
        rule       TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        error      TEXT,
        created_at TEXT NOT NULL,
        saved_at   TEXT
    );
    """,
    # change-analyst output, cached per change and content hash.
    """
    CREATE TABLE analyses (
        change_id    TEXT PRIMARY KEY REFERENCES changes(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL,
        status       TEXT NOT NULL,
        analysis     TEXT,
        error        TEXT,
        run_id       TEXT,
        updated_at   TEXT NOT NULL
    );
    """,
)
LESSON_STATUSES = ("pending", "saved", "failed")

#: A partner is stale once one of its sources fails this many sweeps in a row.
STALE_AFTER_FAILURES = 2
SOURCE_STATUSES = ("ok", "failing", "unsupported")

# Two effective dates do not contradict each other if either is missing, they are
# equal, or one is month-precision ("May 2026") and the other falls in that month.
_DATES_COMPATIBLE = (
    "(effective_at IS NULL OR :d IS NULL OR effective_at = :d OR "
    "(substr(effective_at, 1, 7) = substr(:d, 1, 7) AND (effective_precision = 'month' OR :p = 'month')))"
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
    effective_precision: str
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
            "effectivePrecision": self.effective_precision,
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
        params = {
            "title_key": item.title_key,
            "partner": item.partner,
            "kind": item.kind,
            "versions": json.dumps(list(item.versions)),
            "d": item.effective_at,
            "p": item.effective_precision,
        }
        # A matching title from another source must not contradict versions or date.
        row = self._db.execute(
            f"SELECT * FROM changes WHERE title_key = :title_key AND versions = :versions "
            f"AND {_DATES_COMPATIBLE} ORDER BY first_seen LIMIT 1",
            params,
        ).fetchone()
        if row:
            return row
        # A version's sunset is one event per partner, however each source words it.
        # (Not for other kinds: two field deprecations in v21 are different changes.)
        if item.versions and item.kind == "sunset":
            return self._db.execute(
                f"SELECT * FROM changes WHERE partner = :partner AND kind = :kind AND versions = :versions "
                f"AND {_DATES_COMPATIBLE} ORDER BY first_seen LIMIT 1",
                params,
            ).fetchone()
        return None

    def _insert(self, item: NormalizedChange) -> Change:
        now = self._stamp()
        change_id = uuid.uuid4().hex[:16]
        self._db.execute(
            """INSERT INTO changes (id, partner, kind, title, body, versions, published_at, effective_at,
                   effective_precision, source_url, source_kind, content_hash, strong_key, title_key,
                   first_seen, last_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (change_id, item.partner, item.kind, item.title, item.body, json.dumps(list(item.versions)),
             item.published_at, item.effective_at, item.effective_precision, item.source_url, item.source_kind,
             item.content_hash, item.strong_key, item.title_key, now, now, now),
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
        # The effective date is filled in by whichever source has it, sharpened from
        # a month to a day when a more precise source agrees, and may be moved by the
        # primary source (a partner pushing a sunset date back).
        if item.effective_at:
            fills = row["effective_at"] is None
            same_month = row["effective_at"] is not None and row["effective_at"][:7] == item.effective_at[:7]
            sharpens = same_month and row["effective_precision"] == "month" and item.effective_precision == "day"
            # A month-level restatement of a day we already know is not a move.
            blurs = same_month and row["effective_precision"] == "day" and item.effective_precision == "month"
            moves = is_primary and not blurs and (item.effective_at, item.effective_precision) != (
                row["effective_at"], row["effective_precision"])
            if fills or sharpens or moves:
                updates["effective_at"] = item.effective_at
                updates["effective_precision"] = item.effective_precision
        if item.published_at and (row["published_at"] is None or item.published_at < row["published_at"]):
            updates["published_at"] = item.published_at
        if is_primary and item.content_hash != row["content_hash"]:
            updates.update(title=item.title, body=item.body, content_hash=item.content_hash)
        if "effective_at" in updates:
            versions = json.loads(row["versions"])
            updates["strong_key"] = (
                f"{row['partner']}|{row['kind']}|{','.join(versions)}|{updates['effective_at']}"
                if versions and updates["effective_precision"] == "day" else None
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
            effective_at=row["effective_at"], effective_precision=row["effective_precision"],
            source_url=row["source_url"], source_kind=row["source_kind"],
            content_hash=row["content_hash"], status=status, status_reason=row["status_reason"],
            snoozed_until=snoozed_until, triaged_at=row["triaged_at"], first_seen=row["first_seen"],
            last_seen=row["last_seen"], updated_at=row["updated_at"], sources=sources,
        )

    # -- source health ------------------------------------------------------

    def record_source(
        self, partner: str, source_url: str, source_kind: str, *,
        status: str, items: int = 0, error: str | None = None,
    ) -> dict[str, Any]:
        """Record one sweep's outcome for a source. Returns the updated health row.

        ``failing`` increments the consecutive-failure count; ``ok`` resets it;
        ``unsupported`` (no adapter for this source) never counts as a failure.
        """
        if status not in SOURCE_STATUSES:
            raise ValueError(f"unknown source status {status!r}")
        now = self._stamp()
        with self._db:
            prev = self._db.execute(
                "SELECT consecutive_failures, last_success FROM source_health WHERE partner = ? AND source_url = ?",
                (partner, source_url),
            ).fetchone()
            failures = prev["consecutive_failures"] if prev else 0
            last_success = prev["last_success"] if prev else None
            if status == "failing":
                failures += 1
            elif status == "ok":
                failures, last_success, error = 0, now, None
            else:
                failures = 0
            self._db.execute(
                """INSERT INTO source_health (partner, source_url, source_kind, status, consecutive_failures,
                       last_attempt, last_success, last_error, items_last_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (partner, source_url) DO UPDATE SET
                       source_kind = excluded.source_kind, status = excluded.status,
                       consecutive_failures = excluded.consecutive_failures, last_attempt = excluded.last_attempt,
                       last_success = excluded.last_success, last_error = excluded.last_error,
                       items_last_run = excluded.items_last_run""",
                (partner, source_url, source_kind, status, failures, now, last_success, error, items),
            )
        return self.source_health(partner, source_url)[0]

    def source_health(self, partner: str | None = None, source_url: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM source_health WHERE 1=1", []
        if partner is not None:
            sql, args = sql + " AND partner = ?", [*args, partner]
        if source_url is not None:
            sql, args = sql + " AND source_url = ?", [*args, source_url]
        rows = self._db.execute(sql + " ORDER BY partner, source_url", args).fetchall()
        return [
            {
                "partner": r["partner"], "sourceUrl": r["source_url"], "sourceKind": r["source_kind"],
                "status": r["status"], "consecutiveFailures": r["consecutive_failures"],
                "lastAttempt": r["last_attempt"], "lastSuccess": r["last_success"], "lastError": r["last_error"],
                "itemsLastRun": r["items_last_run"], "stale": r["consecutive_failures"] >= STALE_AFTER_FAILURES,
            }
            for r in rows
        ]

    def stale_partners(self) -> dict[str, list[dict[str, Any]]]:
        """Partners with at least one source failing STALE_AFTER_FAILURES sweeps in a row."""
        out: dict[str, list[dict[str, Any]]] = {}
        for h in self.source_health():
            if h["stale"]:
                out.setdefault(h["partner"], []).append(h)
        return out

    # -- triage -------------------------------------------------------------

    def accept(self, change_id: str) -> Change:
        return self._set_status(change_id, "accepted", None, None)

    def dismiss(self, change_id: str, reason: str, lesson_rule: str | None = None) -> Change:
        """Dismiss with a required reason; queue ``lesson_rule`` to be saved as a Crew lesson."""
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("dismissing a change requires a reason")
        change = self._set_status(change_id, "dismissed", reason, None)
        if lesson_rule:
            with self._db:
                self._db.execute(
                    "INSERT INTO lessons (change_id, rule, status, created_at) VALUES (?, ?, 'pending', ?) "
                    "ON CONFLICT (change_id) DO UPDATE SET rule = excluded.rule, status = 'pending', "
                    "error = NULL, created_at = excluded.created_at, saved_at = NULL",
                    (change_id, lesson_rule, self._stamp()),
                )
        return change

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
        change = self._set_status(change_id, "new", None, None)
        # A lesson not yet saved into Crew is withdrawn; a saved one stays in Crew.
        with self._db:
            self._db.execute("DELETE FROM lessons WHERE change_id = ? AND status != 'saved'", (change_id,))
        return change

    # -- lessons ------------------------------------------------------------

    def lesson(self, change_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM lessons WHERE change_id = ?", (change_id,)).fetchone()
        return self._lesson_dict(row) if row else None

    def lessons(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM lessons", ()
        if status is not None:
            sql, args = sql + " WHERE status = ?", (status,)
        return [self._lesson_dict(r) for r in self._db.execute(sql + " ORDER BY created_at, change_id", args)]

    def mark_lesson(self, change_id: str, status: str, error: str | None = None) -> None:
        if status not in LESSON_STATUSES:
            raise ValueError(f"unknown lesson status {status!r}")
        with self._db:
            self._db.execute(
                "UPDATE lessons SET status = ?, error = ?, saved_at = ? WHERE change_id = ?",
                (status, error, self._stamp() if status == "saved" else None, change_id),
            )

    # -- analyses -----------------------------------------------------------

    def save_analysis(self, change_id: str, content_hash: str, *, status: str,
                      analysis: dict[str, Any] | None = None, error: str | None = None,
                      run_id: str | None = None) -> None:
        with self._db:
            self._db.execute(
                """INSERT INTO analyses (change_id, content_hash, status, analysis, error, run_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (change_id) DO UPDATE SET content_hash = excluded.content_hash,
                       status = excluded.status, analysis = COALESCE(excluded.analysis, analyses.analysis),
                       error = excluded.error, run_id = COALESCE(excluded.run_id, analyses.run_id),
                       updated_at = excluded.updated_at""",
                (change_id, content_hash, status, json.dumps(analysis) if analysis is not None else None,
                 error, run_id, self._stamp()),
            )

    def analysis(self, change_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM analyses WHERE change_id = ?", (change_id,)).fetchone()
        if not row:
            return None
        return {"status": row["status"], "contentHash": row["content_hash"],
                "analysis": json.loads(row["analysis"]) if row["analysis"] else None,
                "error": row["error"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _lesson_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {"changeId": row["change_id"], "rule": row["rule"], "status": row["status"], "error": row["error"],
                "createdAt": row["created_at"], "savedAt": row["saved_at"]}

    def _set_status(self, change_id: str, status: str, reason: str | None, until: str | None) -> Change:
        with self._db:
            cur = self._db.execute(
                "UPDATE changes SET status = ?, status_reason = ?, snoozed_until = ?, triaged_at = ? WHERE id = ?",
                (status, reason, until, self._stamp(), change_id),
            )
        if cur.rowcount == 0:
            raise KeyError(change_id)
        return self._get(change_id)
