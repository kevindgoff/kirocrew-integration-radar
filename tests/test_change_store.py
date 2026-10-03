import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.change_store import MIGRATIONS, ChangeStore
from radar.normalize import RawItem, normalize

SUNSET_PAGE = "https://docs.alpha.example/sunset"
NOTES = "https://docs.alpha.example/notes"
FEED = "https://blog.alpha.example/feed"


class Clock:
    def __init__(self):
        self.t = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def item(**kw):
    base = dict(partner="alpha", source_kind="sunset_schedule", source_url=SUNSET_PAGE,
                title="v19 sunset", effective_at="2025-08-01")
    base.update(kw)
    return normalize(RawItem(**base))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = ChangeStore(":memory:", now=self.clock)

    def tearDown(self):
        self.store.close()


class IngestTests(StoreTestCase):
    def test_new_record(self):
        r = self.store.upsert(item())
        self.assertEqual(r.outcome, "new")
        c = r.change
        self.assertEqual((c.partner, c.kind, c.effective_at, c.versions), ("alpha", "sunset", "2025-08-01", ("v19",)))
        self.assertEqual(c.status, "new")
        self.assertEqual(c.sources, (SUNSET_PAGE,))
        self.assertEqual(c.first_seen, "2025-06-01T12:00:00+00:00")

    def test_same_item_again_is_unchanged(self):
        first = self.store.upsert(item()).change
        self.clock.advance(days=1)
        r = self.store.upsert(item())
        self.assertEqual(r.outcome, "unchanged")
        self.assertEqual(r.change.id, first.id)
        self.assertEqual(r.change.last_seen, "2025-06-02T12:00:00+00:00")
        self.assertEqual(r.change.updated_at, first.updated_at)
        self.assertEqual(len(self.store.list()), 1)

    def test_same_announcement_from_two_sources_is_one_record(self):
        # Sunset page and a feed post word it differently but agree on version and date.
        a = self.store.upsert(item())
        b = self.store.upsert(item(source_kind="feed", source_url=FEED, url=FEED + "/post-1",
                                   title="Reminder: API v19 will be turned off on August 1, 2025",
                                   effective_at=None, body="Sunset date: 2025-08-01"))
        # The feed item has no structured date, so it matches on versions (partial key).
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)
        self.assertEqual(b.change.sources, (SUNSET_PAGE, FEED + "/post-1"))
        self.assertEqual(len(self.store.list()), 1)

    def test_strong_key_merge(self):
        a = self.store.upsert(item())
        b = self.store.upsert(item(source_kind="csv_schedule", source_url="https://docs.alpha.example/s.csv",
                                   title="Alpha v19", kind="sunset"))
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)

    def test_title_key_merge_without_versions(self):
        a = self.store.upsert(item(source_kind="changelog", source_url=NOTES, title="Field X is deprecated",
                                   effective_at=None))
        b = self.store.upsert(item(source_kind="feed", source_url=FEED, title="Field X is deprecated!",
                                   effective_at=None))
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)

    def test_undated_record_gains_date_from_second_source(self):
        a = self.store.upsert(item(source_kind="release_notes", source_url=NOTES, title="v19 will sunset soon",
                                   effective_at=None, published_at="2025-01-15"))
        self.assertIsNone(a.change.effective_at)
        b = self.store.upsert(item(published_at="2025-02-01"))
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)
        self.assertEqual(b.change.effective_at, "2025-08-01")
        self.assertEqual(b.change.published_at, "2025-01-15")  # earliest wins
        # Once dated, the strong key lets a third, dated source match directly.
        c = self.store.upsert(item(source_kind="csv_schedule", source_url="https://docs.alpha.example/s.csv",
                                   title="Alpha v19 end"))
        self.assertEqual(c.change.id, a.change.id)

    def test_release_note_naming_migration_target_merges_with_sunset(self):
        a = self.store.upsert(item(title="v20", effective_at="2025-06-04"))
        b = self.store.upsert(item(source_kind="release_notes", source_url=NOTES,
                                   title="v20 will sunset; migrate to v21", effective_at=None))
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)

    def test_different_versions_or_dates_stay_separate(self):
        self.store.upsert(item())
        self.store.upsert(item(title="v20 sunset", effective_at="2025-08-01"))
        self.store.upsert(item(title="v19 sunset", effective_at="2026-02-01", source_url=NOTES))
        self.assertEqual(len(self.store.list()), 3)

    def test_different_partners_or_kinds_stay_separate(self):
        self.store.upsert(item())
        self.store.upsert(item(partner="beta"))
        self.store.upsert(item(title="v19 deprecated", kind="deprecation", effective_at="2025-02-01"))
        self.assertEqual(len(self.store.list()), 3)

    def test_primary_source_edit_is_updated(self):
        a = self.store.upsert(item(body="v19 ends."))
        self.clock.advance(days=1)
        b = self.store.upsert(item(body="v19 ends. Migrate to v21."))
        self.assertEqual(b.outcome, "updated")
        self.assertEqual(b.change.id, a.change.id)
        self.assertEqual(b.change.body, "v19 ends. Migrate to v21.")
        self.assertNotEqual(b.change.content_hash, a.change.content_hash)
        self.assertEqual(b.change.updated_at, "2025-06-02T12:00:00+00:00")

    def test_primary_source_moving_the_date_is_updated(self):
        a = self.store.upsert(item(source_kind="release_notes", source_url=NOTES, title="v19 will sunset",
                                   effective_at=None))
        self.store.upsert(item(source_kind="release_notes", source_url=NOTES, title="v19 will sunset",
                               effective_at="2025-08-01"))
        b = self.store.upsert(item(source_kind="release_notes", source_url=NOTES, title="v19 will sunset",
                                   effective_at="2025-09-01"))
        self.assertEqual(b.outcome, "updated")
        self.assertEqual(b.change.id, a.change.id)
        self.assertEqual(b.change.effective_at, "2025-09-01")

    def test_secondary_source_does_not_overwrite_primary_content(self):
        a = self.store.upsert(item(body="Official text."))
        self.store.upsert(item(source_kind="feed", source_url=FEED, title="Alpha v19", kind="sunset", body="Blog text."))
        b = self.store.upsert(item(source_kind="feed", source_url=FEED, title="Alpha v19", kind="sunset", body="Edited blog."))
        self.assertEqual(b.change.body, "Official text.")
        self.assertEqual(b.change.title, a.change.title)


class DatePrecisionTests(StoreTestCase):
    def test_exact_date_sharpens_month(self):
        a = self.store.upsert(item(title="Alpha v19 sunset", effective_at="August 2025"))
        self.assertEqual((a.change.effective_at, a.change.effective_precision), ("2025-08-01", "month"))
        b = self.store.upsert(item(source_kind="csv_schedule", source_url="https://docs.alpha.example/s.csv",
                                   title="Alpha v19 sunset", effective_at="2025-08-20"))
        self.assertEqual(b.outcome, "merged")
        self.assertEqual(b.change.id, a.change.id)
        self.assertEqual((b.change.effective_at, b.change.effective_precision), ("2025-08-20", "day"))

    def test_month_restatement_never_blurs_exact_date(self):
        self.store.upsert(item(title="Alpha v19 sunset", effective_at="August 2025"))
        self.store.upsert(item(source_kind="csv_schedule", source_url="https://docs.alpha.example/s.csv",
                               title="Alpha v19 sunset", effective_at="2025-08-20"))
        # The month-level primary source reports the same thing again.
        r = self.store.upsert(item(title="Alpha v19 sunset", effective_at="August 2025"))
        self.assertEqual(r.outcome, "unchanged")
        self.assertEqual((r.change.effective_at, r.change.effective_precision), ("2025-08-20", "day"))

    def test_primary_source_moving_to_another_month_is_a_move(self):
        a = self.store.upsert(item(title="Alpha v19 sunset", effective_at="August 2025"))
        b = self.store.upsert(item(title="Alpha v19 sunset", effective_at="October 2025"))
        self.assertEqual((b.outcome, b.change.id, b.change.effective_at), ("updated", a.change.id, "2025-10-01"))

    def test_different_months_from_other_sources_stay_separate(self):
        self.store.upsert(item(title="Alpha v19 sunset", effective_at="August 2025"))
        self.store.upsert(item(source_kind="csv_schedule", source_url="https://docs.alpha.example/s.csv",
                               title="Alpha v19 sunset", effective_at="2025-10-20"))
        self.assertEqual(len(self.store.list()), 2)

    def test_versions_only_merge_is_for_sunsets(self):
        self.store.upsert(item(source_kind="changelog", source_url=NOTES, title="v21: field X deprecated",
                               effective_at=None))
        self.store.upsert(item(source_kind="changelog", source_url=NOTES, title="v21: field Y deprecated",
                               effective_at=None))
        self.assertEqual(len(self.store.list()), 2)


class SourceHealthTests(StoreTestCase):
    def test_counts_and_resets(self):
        url = "https://docs.alpha.example/s.csv"
        h = self.store.record_source("alpha", url, "csv_schedule", status="failing", error="HTTP 503")
        self.assertEqual((h["consecutiveFailures"], h["stale"], h["lastError"]), (1, False, "HTTP 503"))
        h = self.store.record_source("alpha", url, "csv_schedule", status="failing", error="HTTP 503")
        self.assertTrue(h["stale"])
        self.assertEqual(list(self.store.stale_partners()), ["alpha"])
        h = self.store.record_source("alpha", url, "csv_schedule", status="ok", items=4)
        self.assertEqual((h["consecutiveFailures"], h["stale"], h["lastError"], h["itemsLastRun"]), (0, False, None, 4))
        self.assertEqual(h["lastSuccess"], "2025-06-01T12:00:00+00:00")
        self.assertEqual(self.store.stale_partners(), {})

    def test_unsupported_is_not_a_failure(self):
        for _ in range(3):
            h = self.store.record_source("alpha", NOTES, "release_notes", status="unsupported")
        self.assertEqual((h["consecutiveFailures"], h["stale"]), (0, False))

    def test_unknown_status(self):
        with self.assertRaises(ValueError):
            self.store.record_source("alpha", NOTES, "release_notes", status="weird")


class TriageTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.id = self.store.upsert(item()).change.id

    def test_accept(self):
        c = self.store.accept(self.id)
        self.assertEqual(c.status, "accepted")
        self.assertEqual(c.triaged_at, "2025-06-01T12:00:00+00:00")

    def test_dismiss_requires_reason(self):
        with self.assertRaises(ValueError):
            self.store.dismiss(self.id, "  ")
        c = self.store.dismiss(self.id, "We do not use v19")
        self.assertEqual((c.status, c.status_reason), ("dismissed", "We do not use v19"))

    def test_snooze_expires_back_to_new(self):
        c = self.store.snooze(self.id, "2025-06-10")
        self.assertEqual((c.status, c.snoozed_until), ("snoozed", "2025-06-10"))
        self.assertEqual([x.id for x in self.store.list(status="snoozed")], [self.id])
        self.clock.advance(days=9)
        c = self.store.get(self.id)
        self.assertEqual((c.status, c.snoozed_until), ("new", None))
        self.assertEqual([x.id for x in self.store.list(status="new")], [self.id])

    def test_snooze_validation(self):
        with self.assertRaises(ValueError):
            self.store.snooze(self.id, "2025-06-01")  # today is not the future
        with self.assertRaises(ValueError):
            self.store.snooze(self.id, "next week")

    def test_reopen(self):
        self.store.dismiss(self.id, "n/a")
        c = self.store.reopen(self.id)
        self.assertEqual((c.status, c.status_reason), ("new", None))

    def test_unknown_id(self):
        with self.assertRaises(KeyError):
            self.store.accept("nope")

    def test_triage_survives_reingest(self):
        self.store.dismiss(self.id, "not ours")
        r = self.store.upsert(item(body="edited"))
        self.assertEqual(r.outcome, "updated")
        self.assertEqual(r.change.status, "dismissed")

    def test_list_filters(self):
        self.store.upsert(item(partner="beta"))
        self.assertEqual(len(self.store.list(partner="beta")), 1)
        self.store.accept(self.id)
        self.assertEqual(len(self.store.list(status="new")), 1)


class PersistenceTests(unittest.TestCase):
    def test_file_store_persists_and_migrates_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "radar.sqlite"
            s = ChangeStore(path)
            cid = s.upsert(item()).change.id
            s.close()
            s = ChangeStore(path)
            self.assertEqual(s.get(cid).title, "v19 sunset")
            s.close()
            version = sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, len(MIGRATIONS))


if __name__ == "__main__":
    unittest.main()
