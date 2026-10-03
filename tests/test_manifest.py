import textwrap
import unittest
from pathlib import Path

from radar.manifest import validate_text

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "integration-radar.manifest.yaml"

# Line numbers below are referenced by tests; keep the layout stable.
BASE = textwrap.dedent(
    """\
    schema_version: 1
    product: sample-product
    partners:
      alpha:
        display_name: Alpha API
        versioning: major
        sources:
          - kind: changelog
            url: https://docs.alpha.example/changelog
      beta:
        display_name: Beta API
        versioning: dated
        poll: weekly
        sources:
          - kind: csv_schedule
            url: https://beta.example/schedule.csv
    connectors:
      - id: alpha-reporting
        partner: alpha
        api:
          version: v21
        surfaces:
          - service: ReportService
            resources: [report]
            fields: [metrics.clicks]
        criticality: high
      - id: beta-sync
        partner: beta
        lifecycle: active
        api:
          version: v202508
          client: beta-client
          client_version: "1.2.3"
        surfaces:
          - endpoint: /v1/items
        criticality: low
        owner: integrations
        hints:
          tracker_component: Beta
          tracker_labels: [api, beta]
    """
)


def errors(text):
    return [(i.line, i.path, i.message) for i in validate_text(text).errors]


def has_error(text, line, fragment):
    errs = errors(text)
    return any(ln == line and fragment in msg for ln, _, msg in errs), errs


class ExampleManifestTests(unittest.TestCase):
    def test_shipped_example_is_valid(self):
        result = validate_text(EXAMPLE.read_text())
        self.assertTrue(result.ok, [str(e) for e in result.errors])
        self.assertEqual(result.notices, [])
        m = result.manifest
        self.assertEqual(set(m.partners), {"google_ads", "meta", "gam"})
        self.assertEqual(len(m.connectors), 3)
        self.assertEqual(
            m.allowed_hosts,
            {"developers.google.com", "ads-developers.googleblog.com", "developers.facebook.com", "github.com"},
        )


class ValidManifestTests(unittest.TestCase):
    def test_base_is_valid_and_parsed(self):
        result = validate_text(BASE)
        self.assertTrue(result.ok, result.errors)
        m = result.manifest
        self.assertEqual(m.product, "sample-product")
        self.assertEqual(m.partners["alpha"].poll, "daily")
        self.assertEqual(m.partners["beta"].poll, "weekly")
        alpha, beta = m.connectors
        self.assertEqual(alpha.lifecycle, "active")
        self.assertEqual(alpha.action, "fix")
        self.assertEqual(alpha.surfaces[0].kind, "service")
        self.assertEqual(alpha.surfaces[0].fields, ("metrics.clicks",))
        self.assertEqual(beta.surfaces[0].kind, "endpoint")
        self.assertEqual(beta.surfaces[0].resources, ())
        self.assertEqual(beta.api.client_version, "1.2.3")
        self.assertEqual(beta.hints, {"tracker_component": "Beta", "tracker_labels": ["api", "beta"]})
        self.assertEqual(m.allowed_hosts, {"docs.alpha.example", "beta.example"})
        self.assertEqual([c.id for c in m.connectors_for("beta")], ["beta-sync"])

    def test_unquoted_numeric_versions_keep_source_text(self):
        text = BASE.replace("version: v21", "version: 24.0").replace("version: v202508", "version: 202508")
        result = validate_text(text)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual([c.api.version for c in result.manifest.connectors], ["24.0", "202508"])

    def test_semver_and_rolling_versioning(self):
        text = BASE.replace("versioning: major", "versioning: semver").replace("version: v21", "version: 1.4.0")
        self.assertTrue(validate_text(text).ok)
        text = BASE.replace("versioning: major", "versioning: rolling").replace("version: v21", "version: 2025-08-01")
        self.assertTrue(validate_text(text).ok)


class RuleOneUnknownKeysTests(unittest.TestCase):
    def test_unknown_top_level_key(self):
        found, errs = has_error(BASE + "extra: 1\n", 41, "unknown key")
        self.assertTrue(found, errs)

    def test_unknown_connector_key(self):
        text = BASE.replace("    criticality: high\n", "    criticality: high\n    team: x\n")
        found, errs = has_error(text, 27, "unknown key")
        self.assertTrue(found, errs)

    def test_unknown_hint_key(self):
        text = BASE.replace("tracker_component: Beta", "jira_component: Beta")
        found, errs = has_error(text, 39, "unknown key")
        self.assertTrue(found, errs)

    def test_duplicate_key(self):
        text = BASE.replace("product: sample-product\n", "product: sample-product\nproduct: other\n")
        found, errs = has_error(text, 3, "duplicate key (first defined on line 2)")
        self.assertTrue(found, errs)

    def test_missing_required_keys_point_at_parent(self):
        text = BASE.replace("schema_version: 1\n", "")
        found, errs = has_error(text, 1, "missing required key")
        self.assertTrue(found, errs)
        text = BASE.replace("    criticality: high\n", "")
        found, errs = has_error(text, 18, "missing required key")
        self.assertTrue(found, errs)

    def test_schema_version_must_be_integer_one(self):
        self.assertTrue(has_error(BASE.replace("schema_version: 1", "schema_version: 2"), 1, "must be 1")[0])
        self.assertTrue(has_error(BASE.replace("schema_version: 1", 'schema_version: "1"'), 1, "must be 1")[0])

    def test_enum_values(self):
        self.assertTrue(has_error(BASE.replace("poll: weekly", "poll: hourly"), 13, "not one of")[0])
        self.assertTrue(has_error(BASE.replace("kind: changelog", "kind: blog"), 8, "not one of")[0])
        self.assertTrue(has_error(BASE.replace("criticality: low", "criticality: urgent"), 36, "not one of")[0])
        self.assertTrue(has_error(BASE.replace("lifecycle: active", "lifecycle: dead"), 29, "not one of")[0])

    def test_anchors_and_aliases_rejected(self):
        text = BASE.replace("display_name: Alpha API", "display_name: &n Alpha API").replace(
            "display_name: Beta API", "display_name: *n"
        )
        result = validate_text(text)
        self.assertFalse(result.ok)
        self.assertIn("anchors and aliases", result.errors[0].message)

    def test_yaml_syntax_error_has_line(self):
        text = BASE.replace("product: sample-product", "product: [unclosed")
        result = validate_text(text)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("YAML syntax error", result.errors[0].message)
        self.assertGreater(result.errors[0].line, 0)

    def test_empty_and_non_mapping_documents(self):
        self.assertIn("empty", validate_text("").errors[0].message)
        self.assertIn("expected a mapping", validate_text("- a\n- b\n").errors[0].message)

    def test_reports_all_errors_at_once(self):
        text = BASE.replace("extra", "").replace("poll: weekly", "poll: hourly").replace("partner: alpha", "partner: gamma")
        lines = {ln for ln, _, _ in errors(text)}
        self.assertTrue({13, 19} <= lines, lines)


class RuleTwoReferencesAndSurfacesTests(unittest.TestCase):
    def test_unknown_partner_reference(self):
        found, errs = has_error(BASE.replace("partner: alpha", "partner: gamma"), 19, "unknown partner 'gamma'")
        self.assertTrue(found, errs)

    def test_connector_needs_a_surface(self):
        text = BASE.replace(
            "    surfaces:\n      - endpoint: /v1/items\n", "    surfaces: []\n"
        )
        found, errs = has_error(text, 34, "at least 1 item")
        self.assertTrue(found, errs)

    def test_surface_needs_exactly_one_of_service_or_endpoint(self):
        text = BASE.replace("      - endpoint: /v1/items\n", "      - endpoint: /v1/items\n        service: X\n")
        self.assertTrue(has_error(text, 35, "exactly one of")[0])
        text = BASE.replace("      - endpoint: /v1/items\n", "      - fields: [a]\n")
        self.assertTrue(has_error(text, 35, "exactly one of")[0])

    def test_duplicate_connector_id(self):
        text = BASE.replace("id: beta-sync", "id: alpha-reporting")
        found, errs = has_error(text, 27, "duplicate connector id 'alpha-reporting' (first on line 18)")
        self.assertTrue(found, errs)

    def test_ids_must_be_slugs(self):
        self.assertTrue(has_error(BASE.replace("id: beta-sync", "id: Beta Sync"), 27, "slug")[0])
        self.assertTrue(has_error(BASE.replace("  alpha:\n", "  Alpha!:\n"), 4, "partner id")[0])

    def test_partner_needs_sources(self):
        text = BASE.replace(
            "    sources:\n      - kind: changelog\n        url: https://docs.alpha.example/changelog\n",
            "    sources: []\n",
        )
        self.assertTrue(has_error(text, 7, "at least 1 item")[0])


class RuleThreeVersionPatternTests(unittest.TestCase):
    def test_dated_version_on_major_partner(self):
        found, errs = has_error(BASE.replace("version: v21", "version: v202508"), 21, "does not match partner 'alpha' versioning 'major'")
        self.assertTrue(found, errs)

    def test_major_version_on_dated_partner(self):
        found, errs = has_error(BASE.replace("version: v202508", "version: v21"), 31, "versioning 'dated'")
        self.assertTrue(found, errs)

    def test_dated_month_must_be_real(self):
        self.assertTrue(has_error(BASE.replace("version: v202508", "version: v202513"), 31, "does not match")[0])

    def test_semver_requires_three_parts(self):
        text = BASE.replace("versioning: major", "versioning: semver")
        self.assertTrue(has_error(text, 21, "versioning 'semver'")[0])


class RuleFourUrlTests(unittest.TestCase):
    def test_http_rejected(self):
        found, errs = has_error(BASE.replace("https://docs.alpha", "http://docs.alpha"), 9, "https://")
        self.assertTrue(found, errs)

    def test_credentials_in_url_rejected(self):
        self.assertTrue(has_error(BASE.replace("https://docs.alpha", "https://user:pw@docs.alpha"), 9, "credentials")[0])
        self.assertTrue(has_error(BASE.replace("/changelog", "/changelog?access_token=x"), 9, "credential-like query")[0])

    def test_host_is_lowercased(self):
        m = validate_text(BASE.replace("docs.alpha.example", "Docs.Alpha.Example")).manifest
        self.assertIn("docs.alpha.example", m.allowed_hosts)


class RuleFiveSecretKeysTests(unittest.TestCase):
    def test_secret_like_keys_rejected_anywhere(self):
        for key in ("api_key", "token", "client_secret", "Password", "auth_token"):
            text = BASE.replace("    poll: weekly\n", f"    poll: weekly\n    {key}: x\n")
            found, errs = has_error(text, 14, "credential-like keys are not allowed")
            self.assertTrue(found, (key, errs))

    def test_secret_like_partner_id_rejected(self):
        found, errs = has_error(BASE.replace("  alpha:\n", "  secret:\n"), 4, "credential-like")
        self.assertTrue(found, errs)


class RuleSixLifecycleTests(unittest.TestCase):
    def test_legacy_and_retiring_are_flagged(self):
        for lifecycle in ("legacy", "retiring"):
            result = validate_text(BASE.replace("lifecycle: active", f"lifecycle: {lifecycle}"))
            self.assertTrue(result.ok, result.errors)
            self.assertEqual(len(result.notices), 1)
            notice = result.notices[0]
            self.assertEqual(notice.line, 27)
            self.assertEqual(notice.severity, "notice")
            self.assertIn("migrate or retire", notice.message)
            self.assertEqual(result.manifest.connectors[1].action, "migrate or retire")
            self.assertEqual(result.manifest.summary()["connectors"][1]["action"], "migrate or retire")


if __name__ == "__main__":
    unittest.main()
