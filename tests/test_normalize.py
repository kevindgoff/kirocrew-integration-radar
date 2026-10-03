import unittest
from datetime import date, datetime, timedelta, timezone

from radar.normalize import (
    RawItem,
    canonical_version,
    classify,
    extract_versions,
    normalize,
    parse_date,
)


def raw(**kw):
    base = dict(partner="alpha", source_kind="release_notes", source_url="https://docs.alpha.example/notes",
                title="Alpha API v21 released")
    base.update(kw)
    return RawItem(**base)


class VersionTests(unittest.TestCase):
    def test_canonical_version(self):
        self.assertEqual(canonical_version("V24.0"), "v24")
        self.assertEqual(canonical_version("24.1"), "v24.1")
        self.assertEqual(canonical_version("202508"), "v202508")
        self.assertEqual(canonical_version("v21"), "v21")

    def test_extract_versions(self):
        self.assertEqual(extract_versions("Meta v24.0 and V25 changes"), ("v24", "v25"))
        self.assertEqual(extract_versions("v202508 deprecation"), ("v202508",))
        self.assertEqual(extract_versions("Upgrade to v21."), ("v21",))
        self.assertEqual(extract_versions("v9, v10 and v100"), ("v9", "v10", "v100"))

    def test_ignores_paths_and_client_versions(self):
        self.assertEqual(extract_versions("GET /v1/items removed"), ())
        self.assertEqual(extract_versions("client v1.2.3"), ())
        self.assertEqual(extract_versions("dev21 build"), ())

    def test_migration_targets_are_not_affected_versions(self):
        self.assertEqual(extract_versions("v20 will sunset; migrate to v21"), ("v20",))
        self.assertEqual(extract_versions("v19 deprecated in favor of v21"), ("v19",))
        self.assertEqual(extract_versions("v18 replaced by API version v20"), ("v18",))
        self.assertEqual(extract_versions("Upgrade to v21 now"), ("v21",))  # only version mentioned

    def test_title_versions_win_over_body(self):
        self.assertEqual(extract_versions("v21 sunset", "also mentions v22"), ("v21",))
        self.assertEqual(extract_versions("Sunset notice", "v19 and v20"), ("v19", "v20"))


class DateTests(unittest.TestCase):
    def test_formats(self):
        cases = {
            "2025-08-01": "2025-08-01",
            "2025/8/1": "2025-08-01",
            "20250801": "2025-08-01",
            "2025-08-01T10:00:00Z": "2025-08-01",
            "Fri, 01 Aug 2025 10:00:00 GMT": "2025-08-01",
            "August 1st, 2025": "2025-08-01",
            "Aug 1, 2025": "2025-08-01",
            "Sept 3, 2025": "2025-09-03",
            "1 August 2025": "2025-08-01",
            "  August  1,  2025 ": "2025-08-01",
        }
        for text, expected in cases.items():
            self.assertEqual(parse_date(text), expected, text)

    def test_date_objects_and_timezones(self):
        self.assertEqual(parse_date(date(2025, 8, 1)), "2025-08-01")
        late = datetime(2025, 8, 1, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(parse_date(late), "2025-08-02")  # normalized to UTC

    def test_unparseable(self):
        for value in (None, "", "soon", "Q3 2025", "2025-13-01"):
            self.assertIsNone(parse_date(value), value)


class ClassifyTests(unittest.TestCase):
    def test_hint_wins(self):
        self.assertEqual(classify("v21 sunset", "", "release_notes", "deprecation"), "deprecation")
        self.assertEqual(classify("x", "", "release_notes", "SUNSET"), "sunset")

    def test_invalid_hint_ignored(self):
        self.assertEqual(classify("New field added", "", "release_notes", "bogus"), "additive")

    def test_keywords(self):
        self.assertEqual(classify("v19 will sunset", "", "release_notes"), "sunset")
        self.assertEqual(classify("v19 reaches end-of-life", "", "release_notes"), "sunset")
        self.assertEqual(classify("Breaking change to reports", "", "changelog"), "breaking")
        self.assertEqual(classify("Field X will be removed", "", "changelog"), "breaking")
        self.assertEqual(classify("Field X is deprecated", "", "changelog"), "deprecation")
        self.assertEqual(classify("New metrics available", "", "changelog"), "additive")

    def test_sunset_outranks_deprecation_in_same_text(self):
        self.assertEqual(classify("v19 deprecated, sunset on Aug 1", "", "release_notes"), "sunset")

    def test_title_outranks_body(self):
        self.assertEqual(classify("Field X deprecated", "will be removed later", "changelog"), "deprecation")
        self.assertEqual(classify("Release notes", "Field X will be removed", "changelog"), "breaking")

    def test_schedule_sources_default_to_sunset(self):
        self.assertEqual(classify("v202405", "", "csv_schedule"), "sunset")
        self.assertEqual(classify("v202405", "", "release_notes"), "additive")


class NormalizeTests(unittest.TestCase):
    def test_full_record(self):
        c = normalize(raw(title="  Alpha API  v19 sunset ", body="v19 stops\n working.", effective_at="Aug 1, 2025",
                          published_at="2025-02-01", url="https://docs.alpha.example/notes#v19"))
        self.assertEqual(c.title, "Alpha API v19 sunset")
        self.assertEqual(c.body, "v19 stops working.")
        self.assertEqual(c.kind, "sunset")
        self.assertEqual(c.versions, ("v19",))
        self.assertEqual(c.effective_at, "2025-08-01")
        self.assertEqual(c.published_at, "2025-02-01")
        self.assertEqual(c.source_url, "https://docs.alpha.example/notes#v19")
        self.assertEqual(c.strong_key, "alpha|sunset|v19|2025-08-01")
        self.assertEqual(c.title_key, "alpha|sunset|alpha api v19 sunset")
        self.assertEqual(len(c.content_hash), 64)

    def test_adapter_versions_are_canonicalized(self):
        c = normalize(raw(versions=("V24.0", "25.0", " ")))
        self.assertEqual(c.versions, ("v24", "v25"))

    def test_no_strong_key_without_versions_and_date(self):
        self.assertIsNone(normalize(raw(title="v21 sunset")).strong_key)
        self.assertIsNone(normalize(raw(title="Sunset", effective_at="2025-08-01")).strong_key)

    def test_empty_title_dropped(self):
        self.assertIsNone(normalize(raw(title="   ")))

    def test_content_hash_tracks_content_not_whitespace(self):
        a = normalize(raw(body="Field X deprecated."))
        self.assertEqual(a.content_hash, normalize(raw(body="Field X   deprecated.")).content_hash)
        self.assertNotEqual(a.content_hash, normalize(raw(body="Field Y deprecated.")).content_hash)
        self.assertNotEqual(a.content_hash, normalize(raw(body="Field X deprecated.", effective_at="2025-08-01")).content_hash)


if __name__ == "__main__":
    unittest.main()
