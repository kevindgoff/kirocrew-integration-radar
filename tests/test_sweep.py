import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from radar.app import create_app
from radar.change_store import ChangeStore
from radar.manifest import validate_text
from radar.proxy_auth import HEADER, sign
from radar.settings import Settings
from radar.sweep import Sweeper

from fixture_fetcher import GADS_FEED, GAM_CSV, GAM_DEPRECATION, FixtureTransport, redirect

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "integration-radar.manifest.yaml"
TODAY = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)  # the day the fixtures were recorded


class Clock:
    def __init__(self):
        self.t = TODAY

    def __call__(self):
        return self.t


def example_manifest():
    result = validate_text(EXAMPLE.read_text())
    assert result.ok, result.errors
    return result.manifest


class SweepTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = ChangeStore(":memory:", now=self.clock)
        self.transport = FixtureTransport()
        self.sweeper = Sweeper(self.store, fetcher_factory=self.transport.factory(), now=self.clock)
        self.manifest = example_manifest()

    def tearDown(self):
        self.store.close()

    def by_status(self, report):
        out = {}
        for s in report.sources:
            out.setdefault(s.status, []).append((s.partner, s.kind))
        return out


class FullSweepTests(SweepTestCase):
    async def test_example_manifest_sweep(self):
        report = await self.sweeper.run(self.manifest)
        statuses = self.by_status(report)
        self.assertEqual(sorted(statuses["ok"]), [
            ("gam", "csv_schedule"), ("gam", "sunset_schedule"), ("google_ads", "feed"),
            ("google_ads", "sunset_schedule"), ("meta", "sunset_schedule"),
        ])
        self.assertEqual(sorted(statuses["unsupported"]), [
            ("gam", "release_notes"), ("google_ads", "release_notes"), ("meta", "api_spec_repo"), ("meta", "changelog"),
        ])
        self.assertNotIn("failing", statuses)
        self.assertEqual(report.stale_partners, [])
        # Unsupported sources are never fetched.
        self.assertFalse(any("release-notes" in u or "rel_notes" in u or "github.com" in u for u in self.transport.requested))

    async def test_same_announcement_on_two_sources_is_one_record(self):
        await self.sweeper.run(self.manifest)
        gam = self.store.list(partner="gam")
        # 6 versions x (sunset, deprecation) = 12, minus one deprecation older than the age window.
        self.assertEqual(len(gam), 11)
        self.assertTrue(all(len(c.sources) == 2 for c in gam))
        # The deprecation page says "August 2026"; the CSV's exact date wins.
        s = next(c for c in gam if c.title.endswith("v202508 sunset"))
        self.assertEqual((s.effective_at, s.effective_precision), ("2026-08-31", "day"))

    async def test_feed_post_merges_with_schedule_row(self):
        await self.sweeper.run(self.manifest)
        v22 = [c for c in self.store.list(partner="google_ads") if c.kind == "sunset" and c.versions == ("v22",)]
        self.assertEqual(len(v22), 1)
        self.assertEqual(len(v22[0].sources), 2)
        self.assertEqual(v22[0].published_at, "2026-09-02")  # from the feed post
        # The table says "October 2026 (tentative)"; the post says "will sunset on October 7, 2026".
        self.assertEqual((v22[0].effective_at, v22[0].effective_precision), ("2026-10-07", "day"))

    async def test_old_schedule_rows_are_skipped(self):
        report = await self.sweeper.run(self.manifest)
        self.assertGreater(report.skipped_old, 30)  # decades of past API versions
        meta = self.store.list(partner="meta")
        self.assertEqual(sorted(c.versions[0] for c in meta), ["v23", "v24"])

    async def test_second_sweep_changes_nothing(self):
        first = await self.sweeper.run(self.manifest)
        self.assertEqual(first.outcomes["new"], len(self.store.list()))
        second = await self.sweeper.run(self.manifest)
        self.assertEqual(second.outcomes["new"] + second.outcomes["merged"] + second.outcomes["updated"], 0)
        self.assertEqual(second.changed, [])

    async def test_partner_filter(self):
        report = await self.sweeper.run(self.manifest, ["meta"])
        self.assertEqual({s.partner for s in report.sources}, {"meta"})


class StalenessTests(SweepTestCase):
    def break_csv(self):
        self.transport.routes[GAM_CSV] = (503, {}, b"")

    async def test_two_consecutive_failures_mark_partner_stale(self):
        self.break_csv()
        first = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(first.stale_partners, [])
        failing = [s for s in first.sources if s.status == "failing"]
        self.assertEqual(len(failing), 1)
        self.assertIn("HTTP 503", failing[0].error)

        second = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(second.stale_partners, ["gam"])
        self.assertEqual(second.newly_stale, ["gam"])
        third = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(third.newly_stale, [])  # reported once, on the transition
        health = self.store.stale_partners()["gam"][0]
        self.assertEqual((health["sourceUrl"], health["consecutiveFailures"]), (GAM_CSV, 3))

    async def test_success_resets_and_recovers(self):
        self.break_csv()
        await self.sweeper.run(self.manifest, ["gam"])
        await self.sweeper.run(self.manifest, ["gam"])
        self.transport.routes = FixtureTransport().routes
        report = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(report.recovered, ["gam"])
        self.assertEqual(report.stale_partners, [])

    async def test_failures_must_be_consecutive(self):
        self.break_csv()
        await self.sweeper.run(self.manifest, ["gam"])
        good = FixtureTransport().routes[GAM_CSV]
        self.transport.routes[GAM_CSV] = good
        await self.sweeper.run(self.manifest, ["gam"])
        self.break_csv()
        report = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(report.stale_partners, [])

    async def test_layout_change_counts_as_failure(self):
        self.transport.routes[GAM_DEPRECATION] = (200, {"content-type": "text/html"}, b"<html><p>Redesigned!</p></html>")
        await self.sweeper.run(self.manifest, ["gam"])
        report = await self.sweeper.run(self.manifest, ["gam"])
        self.assertEqual(report.stale_partners, ["gam"])
        err = self.store.stale_partners()["gam"][0]["lastError"]
        self.assertIn("layout may have changed", err)

    async def test_redirect_off_allowlist_fails_the_source(self):
        self.transport.routes[GADS_FEED] = redirect("http://feeds.example.net/blog", 302)
        report = await self.sweeper.run(self.manifest, ["google_ads"])
        feed = next(s for s in report.sources if s.kind == "feed")
        self.assertEqual(feed.status, "failing")
        self.assertIn("non-HTTPS", feed.error)
        self.assertNotIn("http://feeds.example.net/blog", self.transport.requested)

    async def test_sources_removed_from_the_manifest_are_forgotten(self):
        # An old URL that failed twice would otherwise keep its partner stale forever.
        old = "https://old.example/feed"
        self.store.record_source("google_ads", old, "feed", status="failing", error="gone")
        self.store.record_source("google_ads", old, "feed", status="failing", error="gone")
        self.assertEqual(list(self.store.stale_partners()), ["google_ads"])
        report = await self.sweeper.run(self.manifest, ["google_ads"])
        self.assertEqual(report.stale_partners, [])
        self.assertNotIn(old, [h["sourceUrl"] for h in self.store.source_health()])

    async def test_unsupported_sources_never_go_stale(self):
        for _ in range(3):
            report = await self.sweeper.run(self.manifest, ["meta"])
        self.assertEqual(report.stale_partners, [])

    async def test_adapter_crash_is_contained(self):
        self.transport.routes[GAM_CSV] = (200, {"content-type": "text/csv"}, b"version,sunsetDate\nv202608,2027-08-31\n")
        import radar.adapters as adapters
        original = adapters.parse_csv_schedule
        adapter = adapters.adapter_for("csv_schedule", "developers.google.com")
        object.__setattr__(adapter, "parse", lambda ctx: 1 / 0)
        try:
            report = await self.sweeper.run(self.manifest, ["gam"])
        finally:
            object.__setattr__(adapter, "parse", original)
        csv_report = next(s for s in report.sources if s.kind == "csv_schedule")
        self.assertEqual(csv_report.status, "failing")
        self.assertIn("internal error", csv_report.error)
        self.assertEqual(next(s for s in report.sources if s.kind == "sunset_schedule").status, "ok")


SECRET = "test-secret"


def signed(method, target, body=b""):
    ts = int(time.time())
    return {HEADER: f"{ts}:{sign(SECRET, ts, method, target, body)}", "Content-Type": "application/json"}


class SweepApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings("integration-radar", 0, Path(self._tmp.name), SECRET, "http://127.0.0.1:5476")
        self.client = TestClient(TestServer(create_app(settings, fetcher_factory=FixtureTransport().factory(), now=lambda: TODAY)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    async def post(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        return await self.client.post(path, data=body, headers=signed("POST", path, body))

    async def get(self, path):
        return await self.client.get(path, headers=signed("GET", path))

    async def test_sweep_requires_a_manifest(self):
        resp = await self.post("/api/sweep")
        self.assertEqual(resp.status, 409)

    async def test_sweep_then_list(self):
        await self.post("/api/manifest/load", {"path": str(EXAMPLE)})
        resp = await self.post("/api/sweep", {"partners": ["gam"]})
        self.assertEqual(resp.status, 200)
        report = await resp.json()
        self.assertEqual(report["outcomes"]["merged"] + report["outcomes"]["new"], 22)
        changes = (await (await self.get("/api/changes?partner=gam")).json())["changes"]
        self.assertEqual(len(changes), 11)
        self.assertEqual({c["partner"] for c in changes}, {"gam"})
        sources = await (await self.get("/api/sources")).json()
        self.assertEqual(sources["stalePartners"], [])
        self.assertEqual({s["status"] for s in sources["sources"]}, {"ok", "unsupported"})

    async def test_bad_partner_filter(self):
        await self.post("/api/manifest/load", {"path": str(EXAMPLE)})
        self.assertEqual((await self.post("/api/sweep", {"partners": "gam"})).status, 400)


if __name__ == "__main__":
    unittest.main()
