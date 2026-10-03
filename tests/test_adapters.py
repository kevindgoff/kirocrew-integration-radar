import unittest

from radar.adapters import (
    ParseContext,
    ParseError,
    adapter_for,
    component_tables,
    html_tables,
    parse,
)
from radar.fetch import FetchResult
from radar.manifest import Partner, Source
from radar.normalize import normalize

from fixture_fetcher import fixture


def ctx(kind, url, body, partner_id="p", name="Partner API", versioning="major", content_type="text/html"):
    host = url.split("/")[2]
    source = Source(kind, url, host)
    partner = Partner(partner_id, name, versioning, (source,))
    return ParseContext(partner, source, FetchResult(url, url, 200, content_type, body))


def by_title(items):
    return {normalize(i).title: normalize(i) for i in items}


class RegistryTests(unittest.TestCase):
    def test_host_specific_adapter_wins(self):
        self.assertEqual(adapter_for("sunset_schedule", "developers.facebook.com").name, "rendered-schedule")
        self.assertEqual(adapter_for("sunset_schedule", "DEVELOPERS.FACEBOOK.COM").name, "rendered-schedule")

    def test_generic_fallback(self):
        self.assertEqual(adapter_for("sunset_schedule", "developers.google.com").name, "html-schedule")
        self.assertEqual(adapter_for("csv_schedule", "any.example").name, "csv-schedule")
        self.assertEqual(adapter_for("feed", "any.example").name, "feed")

    def test_unsupported_kinds(self):
        for kind in ("release_notes", "changelog", "out_of_cycle", "api_spec_repo"):
            self.assertIsNone(adapter_for(kind, "developers.google.com"), kind)
        with self.assertRaises(ParseError):
            parse(ctx("release_notes", "https://x.example/notes", b""))


class CsvScheduleTests(unittest.TestCase):
    def setUp(self):
        self.items = parse(ctx("csv_schedule", "https://developers.google.com/ad-manager/downloads/api-schedule.csv",
                               fixture("gam-api-schedule.csv"), "gam", "Ad Manager API", "dated", "text/csv"))

    def test_one_sunset_and_one_deprecation_per_version(self):
        self.assertEqual(len(self.items), 12)
        changes = by_title(self.items)
        s = changes["Ad Manager API v202508 sunset"]
        self.assertEqual((s.kind, s.versions, s.effective_at, s.effective_precision),
                         ("sunset", ("v202508",), "2026-08-31", "day"))
        d = changes["Ad Manager API v202508 deprecation"]
        self.assertEqual((d.kind, d.effective_at), ("deprecation", "2026-05-29"))
        self.assertIn("Released 2025-08-20", s.body)

    def test_html_instead_of_csv_is_an_error(self):
        with self.assertRaisesRegex(ParseError, "HTML"):
            parse(ctx("csv_schedule", "https://x.example/s.csv", b"<!doctype html><p>Moved</p>"))

    def test_csv_without_schedule_columns_is_an_error(self):
        with self.assertRaisesRegex(ParseError, "no version schedule"):
            parse(ctx("csv_schedule", "https://x.example/s.csv", b"name,value\na,1\n"))


class HtmlScheduleTests(unittest.TestCase):
    def test_month_level_sunset_table(self):
        items = parse(ctx("sunset_schedule", "https://developers.google.com/google-ads/api/docs/sunset-dates",
                          fixture("google-ads-sunset-dates.html"), "google_ads", "Ads API"))
        changes = by_title(items)
        self.assertEqual(len(items), 12)
        v22 = changes["Ads API v22 sunset"]
        self.assertEqual((v22.effective_at, v22.effective_precision), ("2026-10-01", "month"))
        self.assertIn("(tentative)", v22.body)
        self.assertIn("Ads API v26.1 sunset", changes)  # "v26.1*" footnote marker stripped
        self.assertIn("Ads API v23.2 sunset", changes)

    def test_deprecation_page_fallback_for_dated_versions(self):
        items = parse(ctx("sunset_schedule", "https://developers.google.com/ad-manager/api/deprecation",
                          fixture("gam-deprecation.html"), "gam", "Ad Manager API", "dated"))
        changes = by_title(items)
        self.assertEqual(len(items), 12)
        s = changes["Ad Manager API v202508 sunset"]
        self.assertEqual((s.effective_at, s.effective_precision), ("2026-08-01", "month"))

    def test_page_without_schedule_is_an_error(self):
        with self.assertRaisesRegex(ParseError, "layout may have changed"):
            parse(ctx("sunset_schedule", "https://x.example/s", b"<html><table><tr><th>Name</th></tr></table></html>"))

    def test_html_tables_ignores_script_text(self):
        tables = html_tables("<table><tr><th>A<script>var x='<td>'</script></th><td>b<br>c</td></tr></table>")
        self.assertEqual(tables, [[["A", "b c"]]])


class RenderedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.items = parse(ctx("sunset_schedule",
                               "https://developers.facebook.com/docs/marketing-api/marketing-api-changelog/versions",
                               fixture("meta-versions.html"), "meta", "Marketing API"))

    def test_component_json_table(self):
        changes = by_title(self.items)
        v24 = changes["Marketing API v24 sunset"]  # "v24.0" canonicalized
        self.assertEqual((v24.effective_at, v24.effective_precision), ("2026-10-06", "day"))
        self.assertIn("Released October 8, 2025", v24.body)

    def test_tbd_expirations_are_skipped(self):
        titles = by_title(self.items)
        self.assertNotIn("Marketing API v25 sunset", titles)
        self.assertNotIn("Marketing API v26 sunset", titles)

    def test_component_tables_header(self):
        tables = component_tables(fixture("meta-versions.html").decode())
        self.assertEqual(tables[0][0], ["Version", "Release Date", "Expiration Date"])


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.items = parse(ctx("feed", "https://feeds.feedburner.com/GoogleAdsDeveloperBlog",
                               fixture("ads-developer-blog.atom.xml"), "google_ads", "Ads API",
                               content_type="text/xml"))

    def test_atom_entries(self):
        self.assertEqual(len(self.items), 4)
        changes = by_title(self.items)
        reminder = changes["Google Ads API v22 sunset reminder"]
        self.assertEqual((reminder.kind, reminder.versions, reminder.published_at), ("sunset", ("v22",), "2026-09-02"))
        self.assertTrue(reminder.source_url.startswith("https://"))
        self.assertNotIn("<", reminder.body)  # HTML stripped

    def test_body_does_not_drive_classification(self):
        # This post's body mentions versions and sunsets in passing; its title does not.
        onboarding = by_title(self.items)["A new onboarding experience for Google Ads API developers"]
        self.assertEqual((onboarding.kind, onboarding.versions), ("additive", ()))

    def test_rss(self):
        rss = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
               b'<item><title>v19 sunset on Aug 1</title><link>https://x.example/p/1</link>'
               b'<pubDate>Fri, 01 Aug 2025 10:00:00 GMT</pubDate><description>&lt;p&gt;Hi&lt;/p&gt;</description></item>'
               b'<item><title>Plain link</title><link>http://x.example/p/2</link></item>'
               b'</channel></rss>')
        items = parse(ctx("feed", "https://x.example/rss", rss))
        first, second = (normalize(i) for i in items)
        self.assertEqual((first.kind, first.published_at, first.source_url, first.body),
                         ("sunset", "2025-08-01", "https://x.example/p/1", "Hi"))
        self.assertEqual(second.source_url, "https://x.example/rss")  # http permalink not kept

    def test_entity_declarations_refused(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
        with self.assertRaisesRegex(ParseError, "entities"):
            parse(ctx("feed", "https://x.example/rss", evil))

    def test_not_a_feed(self):
        with self.assertRaisesRegex(ParseError, "not an RSS or Atom"):
            parse(ctx("feed", "https://x.example/rss", b"<html><body/></html>"))
        with self.assertRaisesRegex(ParseError, "not valid XML"):
            parse(ctx("feed", "https://x.example/rss", b"<rss"))
        with self.assertRaisesRegex(ParseError, "no entries"):
            parse(ctx("feed", "https://x.example/rss", b"<rss><channel/></rss>"))


if __name__ == "__main__":
    unittest.main()
