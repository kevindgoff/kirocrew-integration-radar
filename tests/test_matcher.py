import json
import tempfile
import time
import textwrap
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from radar.app import create_app
from radar.change_store import ChangeStore
from radar.manifest import validate_text
from radar.matcher import match_all, match_change, rank
from radar.normalize import RawItem, normalize
from radar.proxy_auth import HEADER, sign
from radar.settings import Settings

MANIFEST = textwrap.dedent(
    """\
    schema_version: 1
    product: sample-product
    partners:
      ads:
        display_name: Ads API
        versioning: major
        sources:
          - kind: changelog
            url: https://docs.ads.example/changelog
      social:
        display_name: Social API
        versioning: major
        sources:
          - kind: changelog
            url: https://docs.social.example/changelog
      mgr:
        display_name: Manager API
        versioning: dated
        sources:
          - kind: csv_schedule
            url: https://docs.mgr.example/schedule.csv
    connectors:
      - id: ads-reporting
        partner: ads
        api:
          version: v21
        surfaces:
          - service: SearchService.SearchStream
            resources: [campaign, ad_group]
            fields: [metrics.clicks, segments.date]
        criticality: high
      - id: social-insights
        partner: social
        api:
          version: v24.0
        surfaces:
          - endpoint: /{ad-account-id}/insights
            resources: [adset, campaign]
            fields: [impressions, spend, cpm]
        criticality: medium
      - id: social-legacy
        partner: social
        lifecycle: legacy
        api:
          version: v22.0
        surfaces:
          - endpoint: /{page-id}/feed
        criticality: low
      - id: mgr-proposals
        partner: mgr
        api:
          version: v202508
        surfaces:
          - service: ProposalService
            resources: [ProposalLineItem]
        criticality: medium
    """
)
TODAY = date(2026, 10, 3)


def manifest():
    result = validate_text(MANIFEST)
    assert result.ok, result.errors
    return result.manifest


class Fixture(unittest.TestCase):
    def setUp(self):
        self.store = ChangeStore(":memory:", now=lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
        self.m = manifest()
        self.connectors = {c.id: c for c in self.m.connectors}

    def tearDown(self):
        self.store.close()

    def change(self, partner="ads", title="Update", body="", **kw):
        kw.setdefault("source_kind", "changelog")
        kw.setdefault("source_url", f"https://docs.{partner}.example/changelog")
        item = normalize(RawItem(partner=partner, title=title, body=body, **kw))
        return self.store.upsert(item).change

    def match(self, change, connector_id):
        return match_change(change, self.connectors[connector_id])


class VersionMatchTests(Fixture):
    def test_sunset_of_pinned_version_is_exact(self):
        m = self.match(self.change(title="Ads API v21 sunset", effective_at="2026-11-01"), "ads-reporting")
        self.assertEqual(m.confidence, "exact")
        self.assertEqual(m.reasons, ("sunset of pinned version v21",))

    def test_pinned_version_is_canonicalized(self):
        # Connector pins "v24.0"; the change says "v24".
        m = self.match(self.change("social", "v24 deprecated", kind="deprecation"), "social-insights")
        self.assertEqual(m.confidence, "exact")

    def test_dated_versions(self):
        m = self.match(self.change("mgr", "Manager API v202508 sunset", effective_at="2026-08-31"), "mgr-proposals")
        self.assertEqual(m.confidence, "exact")

    def test_other_change_in_pinned_version_is_likely(self):
        m = self.match(self.change(title="v21: breaking change to error codes"), "ads-reporting")
        self.assertEqual((m.confidence, m.reasons), ("likely", ("breaking change in pinned version v21",)))
        m = self.match(self.change(title="New in v21: audience insights"), "ads-reporting")
        self.assertEqual(m.confidence, "likely")

    def test_other_versions_do_not_match(self):
        self.assertIsNone(self.match(self.change(title="Ads API v22 sunset", effective_at="2026-11-01"), "ads-reporting"))
        self.assertIsNone(self.match(self.change(title="Announcing v25"), "ads-reporting"))

    def test_other_partner_never_matches(self):
        self.assertIsNone(self.match(self.change("social", "v21 sunset metrics.clicks"), "ads-reporting"))


class FieldMatchTests(Fixture):
    def test_identifier_field_is_exact(self):
        m = self.match(self.change(title="Field metrics.clicks will be removed"), "ads-reporting")
        self.assertEqual(m.confidence, "exact")
        self.assertIn("names field metrics.clicks", m.reasons)

    def test_identifier_field_in_body_with_trailing_punctuation(self):
        m = self.match(self.change(title="Reporting changes", body="We are renaming segments.date."), "ads-reporting")
        self.assertEqual(m.confidence, "exact")

    def test_identifier_prefix_is_not_a_match(self):
        self.assertIsNone(self.match(self.change(title="Additions", body="New metrics.clicks_share column"), "ads-reporting"))

    def test_plain_word_field_needs_field_context(self):
        for body in ("The spend field is deprecated.", "Deprecating the field spend.", "The `spend` value changes.",
                     "metric impressions now counts views", "Rename 'impressions' to 'views'"):
            m = self.match(self.change("social", "Insights update", body), "social-insights")
            self.assertEqual(m.confidence, "exact", body)

    def test_plain_word_without_context_is_possible(self):
        m = self.match(self.change("social", "Legacy budget tools deprecated",
                                   "Advertisers can spend smarter and get more impressions."), "social-insights")
        self.assertEqual(m.confidence, "possible")
        self.assertTrue(all("not clearly as a field" in r for r in m.reasons))

    def test_plain_words_in_additive_news_do_not_match(self):
        c = self.change("social", "New guide: spend smarter, earn more impressions per campaign")
        self.assertEqual(c.kind, "additive")
        self.assertIsNone(self.match(c, "social-insights"))
        # Identifiers still count in additive news.
        self.assertEqual(self.match(self.change(title="New: metrics.clicks breakdowns"), "ads-reporting").confidence, "exact")

    def test_short_plain_words_are_ignored(self):
        self.assertIsNone(self.match(self.change("social", "Our CPM explainer"), "social-insights"))


class SurfaceMatchTests(Fixture):
    def test_service_and_method_names_are_likely(self):
        for title in ("SearchService.SearchStream latency", "SearchStream now paginates", "Changes to SearchService"):
            m = self.match(self.change(title=title), "ads-reporting")
            self.assertEqual(m.confidence, "likely", title)

    def test_endpoint_path_or_edge_is_likely(self):
        for title in ("Changes to the /insights edge", "Insights API rate limits", "insights endpoint pagination"):
            m = self.match(self.change("social", title), "social-insights")
            self.assertEqual(m.confidence, "likely", title)

    def test_endpoint_word_alone_is_possible(self):
        m = self.match(self.change("social", "Business insights for creators are deprecated"), "social-insights")
        self.assertEqual(m.confidence, "possible")

    def test_resources(self):
        self.assertEqual(self.match(self.change(title="ad_group limits raised"), "ads-reporting").confidence, "likely")
        self.assertEqual(self.match(self.change("mgr", "ProposalLineItem budget rules"), "mgr-proposals").confidence, "likely")
        self.assertEqual(self.match(self.change(title="Campaign drafts are deprecated"), "ads-reporting").confidence,
                         "possible")

    def test_strongest_signal_wins_and_all_reasons_kept(self):
        m = self.match(self.change(title="v21 sunset", body="Migrate SearchStream calls; metrics.clicks moves.",
                                   effective_at="2026-11-01"), "ads-reporting")
        self.assertEqual(m.confidence, "exact")
        self.assertEqual(set(m.reasons), {"sunset of pinned version v21", "names field metrics.clicks",
                                          "names service SearchService.SearchStream"})


class PartnerWideTests(Fixture):
    def test_unversioned_breaking_news_is_possible(self):
        m = self.match(self.change(title="Breaking: new authentication requirements"), "ads-reporting")
        self.assertEqual((m.confidence, m.reasons), ("possible", ("partner-wide breaking with no version stated",)))

    def test_unversioned_additive_news_does_not_match(self):
        self.assertIsNone(self.match(self.change(title="New developer forum launched"), "ads-reporting"))


class LifecycleTests(Fixture):
    def test_legacy_connector_action(self):
        m = self.match(self.change("social", "v22 sunset", effective_at="2026-02-19"), "social-legacy")
        self.assertEqual((m.confidence, m.action), ("exact", "migrate or retire"))
        m = self.match(self.change("social", "v24 sunset", effective_at="2026-10-06"), "social-insights")
        self.assertEqual(m.action, "fix")

    def test_match_all_orders_by_confidence_then_criticality(self):
        c = self.change("social", "Changes to the /insights edge and /feed edge")
        self.assertEqual([m.connector for m in match_all(c, self.m.connectors)], ["social-insights", "social-legacy"])


class RankingTests(Fixture):
    def ranked(self):
        return rank(self.store.list(), self.m, TODAY)

    def test_impact_and_deadline_order(self):
        far_exact = self.change(title="v21 sunset", effective_at="2027-09-01")  # high x exact = 9
        soon_exact = self.change("mgr", "v202508 sunset", effective_at="2026-10-20")  # medium x exact = 6, +6
        unmatched = self.change(title="Announcing v25")
        possible = self.change(title="Campaign drafts are deprecated")  # high x possible = 3
        order = [r.change.id for r in self.ranked()]
        self.assertEqual(order, [soon_exact.id, far_exact.id, possible.id, unmatched.id])

    def test_urgent_means_matched_and_under_30_days(self):
        self.change("mgr", "v202508 sunset", effective_at="2026-10-20")
        self.change(title="Ads API v22 sunset", effective_at="2026-10-10")  # unmatched
        self.change(title="v21 sunset", effective_at="2026-11-20")  # 48 days
        by_title = {r.change.title: r for r in self.ranked()}
        self.assertTrue(by_title["v202508 sunset"].urgent)
        self.assertEqual(by_title["v202508 sunset"].days_left, 17)
        self.assertFalse(by_title["Ads API v22 sunset"].urgent)
        self.assertFalse(by_title["v21 sunset"].urgent)

    def test_overdue_is_urgent(self):
        self.change("mgr", "v202508 sunset", effective_at="2026-08-31")
        r = self.ranked()[0]
        self.assertEqual((r.days_left, r.urgent), (-33, True))

    def test_ties_go_to_nearest_deadline(self):
        later = self.change(title="v21 sunset", effective_at="2027-06-01")
        sooner = self.change(title="v21 deprecated", kind="deprecation", effective_at="2027-03-01")
        self.assertEqual([r.change.id for r in self.ranked()], [sooner.id, later.id])

    def test_without_manifest_nothing_matches(self):
        self.change(title="v21 sunset", effective_at="2026-10-20")
        r = rank(self.store.list(), None, TODAY)[0]
        self.assertEqual((r.matches, r.impact, r.score, r.urgent), ((), None, 0, False))

    def test_to_dict(self):
        self.change("mgr", "v202508 sunset", effective_at="2026-10-20")
        d = self.ranked()[0].to_dict()
        self.assertEqual(d["impact"], "exact")
        self.assertEqual(d["matches"][0]["connector"], "mgr-proposals")
        self.assertEqual((d["daysLeft"], d["urgent"], d["score"]), (17, True, 12))


SECRET = "test-secret"


class FeedApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        path = root / "m.yaml"
        path.write_text(MANIFEST)
        now = lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc)  # noqa: E731
        app = create_app(Settings("integration-radar", 0, root / "home", SECRET, "http://127.0.0.1:5476"), now=now)
        from radar.app import RADAR_KEY

        app[RADAR_KEY].manifest.load_path(str(path))
        store = app[RADAR_KEY].changes
        for title, partner, eff in (("Announcing v25", "ads", None), ("v202508 sunset", "mgr", "2026-10-20")):
            store.upsert(normalize(RawItem(partner, "changelog", f"https://docs.{partner}.example/changelog",
                                           title, effective_at=eff)))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    async def get(self, path):
        ts = int(time.time())
        resp = await self.client.get(path, headers={HEADER: f"{ts}:{sign(SECRET, ts, 'GET', path, b'')}"})
        self.assertEqual(resp.status, 200)
        return (await resp.json())["changes"]

    async def test_ranked_feed(self):
        changes = await self.get("/api/changes")
        self.assertEqual([c["title"] for c in changes], ["v202508 sunset", "Announcing v25"])
        self.assertEqual(changes[0]["impact"], "exact")
        self.assertIsNone(changes[1]["impact"])

    async def test_matched_only(self):
        changes = await self.get("/api/changes?matched=1")
        self.assertEqual([c["title"] for c in changes], ["v202508 sunset"])


if __name__ == "__main__":
    unittest.main()
