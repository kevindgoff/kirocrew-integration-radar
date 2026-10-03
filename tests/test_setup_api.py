"""End-to-end Setup API tests against a fake Crew gateway."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from radar.app import create_app
from radar.proxy_auth import HEADER, sign

from fake_gateway import ATLASSIAN_TOOLS, FakeGateway, interface, mcp_server
from fixture_fetcher import FixtureTransport

SECRET = "test-secret"
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "integration-radar.manifest.yaml"
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def signed(method, target, body=b""):
    ts = int(time.time())
    return {HEADER: f"{ts}:{sign(SECRET, ts, method, target, body)}", "Content-Type": "application/json"}


class SetupApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.gw = FakeGateway()
        await self.gw.start()
        settings = self.gw.settings(self.root / "home", SECRET)
        app = create_app(settings, fetcher_factory=FixtureTransport().factory(), now=lambda: NOW)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.gw.close()
        self._tmp.cleanup()

    async def call(self, method, path, payload=None, expect=200):
        body = json.dumps(payload).encode() if payload is not None else b""
        resp = await self.client.request(method, path, data=body or None, headers=signed(method, path, body))
        data = await resp.json()
        self.assertEqual(resp.status, expect, data)
        return data

    async def roles(self, refresh=True):
        data = await self.call("POST", "/api/setup/detect") if refresh else await self.call("GET", "/api/setup")
        return data["roles"]


class AcceptanceTests(SetupApiTestCase):
    async def test_only_slack_proposed_and_channel_picked_from_list(self):
        self.gw.interfaces["slack"] = interface(True)
        self.gw.slack_channels = [{"id": "dm", "name": "Direct Message"}, {"id": "C123", "name": "#api-changes"}]
        chat = (await self.roles())["chat"]
        self.assertEqual((chat["status"], chat["proposal"]), ("proposed", "interface:slack"))

        dests = await self.call("GET", "/api/setup/roles/chat/destinations?candidate=interface:slack")
        self.assertEqual(dests["destinations"], [
            {"id": "dm", "name": "Direct message to the interface owner"},
            {"id": "channel:C123", "name": "#api-changes"},
        ])
        data = await self.call("PUT", "/api/setup/roles/chat",
                               {"candidate": "interface:slack", "destination": {"id": "channel:C123"}})
        chat = data["roles"]["chat"]
        self.assertEqual(chat["status"], "bound")
        # The stored name comes from the live list, not from the request.
        self.assertEqual(chat["binding"]["destination"], {"id": "channel:C123", "name": "#api-changes"})
        self.assertEqual(self.gw.config["roles"]["chat"]["candidate"], "interface:slack")

    async def test_slack_and_teams_must_be_chosen(self):
        self.gw.interfaces["slack"] = interface(True)
        self.gw.interfaces["teams"] = interface(True)
        chat = (await self.roles())["chat"]
        self.assertEqual(chat["status"], "needs_choice")
        self.assertEqual({c["id"] for c in chat["candidates"]}, {"interface:slack", "interface:teams"})
        data = await self.call("PUT", "/api/setup/roles/chat",
                               {"candidate": "interface:teams", "destination": {"id": "dm"}})
        self.assertEqual(data["roles"]["chat"]["status"], "bound")

    async def test_no_tracker_is_off_and_feed_still_works(self):
        roles = await self.roles()
        self.assertEqual(roles["tracker"]["status"], "off")
        await self.call("POST", "/api/manifest/load", {"path": str(EXAMPLE)})
        await self.call("POST", "/api/sweep")
        feed = await self.call("GET", "/api/changes?matched=1")
        self.assertTrue(feed["changes"])


class BindingTests(SetupApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gw.mcp.append(mcp_server("atlassian-rovo-mcp", ATLASSIAN_TOOLS))
        self.gw.mcp.append(mcp_server("atlassian", [], status="needs_auth"))
        self.gw.interfaces["slack"] = interface(True)

    async def test_real_world_detection(self):
        roles = await self.roles()
        for role in ("tracker", "wiki", "repo"):
            self.assertEqual((roles[role]["status"], roles[role]["proposal"]), ("proposed", "mcp:atlassian-rovo-mcp"))
        self.assertEqual(roles["chat"]["proposal"], "interface:slack")

    async def test_bind_tracker_without_destination(self):
        data = await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:atlassian-rovo-mcp"})
        self.assertEqual(data["roles"]["tracker"]["status"], "bound")
        await self.call("PUT", "/api/setup/roles/tracker",
                        {"candidate": "mcp:atlassian-rovo-mcp", "destination": {"id": "X"}}, expect=400)

    async def test_bound_tool_vanishing_degrades(self):
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:atlassian-rovo-mcp"})
        self.gw.mcp = [s for s in self.gw.mcp if s["name"] != "atlassian-rovo-mcp"]
        tracker = (await self.roles())["tracker"]
        self.assertEqual(tracker["status"], "degraded")
        self.assertEqual(tracker["binding"]["candidate"], "mcp:atlassian-rovo-mcp")  # kept for when it returns

    async def test_rejections(self):
        await self.call("PUT", "/api/setup/roles/oracle", {"candidate": "x"}, expect=404)
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:nope"}, expect=404)
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "interface:slack"}, expect=404)
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": 5}, expect=400)
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:atlassian-rovo-mcp", "x": 1}, expect=400)
        # Chat requires a destination, and only one from the live list.
        await self.call("PUT", "/api/setup/roles/chat", {"candidate": "interface:slack"}, expect=400)
        await self.call("PUT", "/api/setup/roles/chat",
                        {"candidate": "interface:slack", "destination": {"id": "channel:CGUESSED"}}, expect=409)
        self.assertEqual(self.gw.config, {})

    async def test_unusable_candidate_cannot_be_bound(self):
        self.gw.mcp.append(mcp_server("jira-pending", ["getJiraIssue"], status="needs_auth"))
        data = await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:jira-pending"}, expect=409)
        self.assertIn("needs_auth", data["error"])

    async def test_switch_off_and_back_to_automatic(self):
        data = await self.call("PUT", "/api/setup/roles/chat", {"off": True})
        self.assertEqual(data["roles"]["chat"]["status"], "off")
        data = await self.call("PUT", "/api/setup/roles/chat", {"candidate": None})
        self.assertEqual(data["roles"]["chat"]["status"], "proposed")
        self.assertEqual(self.gw.config["roles"], {})

    async def test_bindings_preserve_other_config(self):
        self.gw.config = {"manifestPath": "/x.yaml", "schedule": {"sweepTime": "07:00"}}
        await self.call("PUT", "/api/setup/roles/tracker", {"candidate": "mcp:atlassian-rovo-mcp"})
        self.assertEqual(self.gw.config["manifestPath"], "/x.yaml")
        self.assertEqual(self.gw.config["schedule"], {"sweepTime": "07:00"})

    async def test_non_chat_destinations_not_yet(self):
        await self.call("GET", "/api/setup/roles/tracker/destinations?candidate=mcp:atlassian-rovo-mcp", expect=400)
        await self.call("GET", "/api/setup/roles/chat/destinations?candidate=interface:teams", expect=404)


class DestinationTests(SetupApiTestCase):
    async def test_non_slack_interfaces_offer_dm_and_configured_targets(self):
        self.gw.interfaces["discord"] = interface(True, allowed_channel_ids=["111", "222"])
        await self.roles()
        dests = await self.call("GET", "/api/setup/roles/chat/destinations?candidate=interface:discord")
        self.assertEqual([d["id"] for d in dests["destinations"]], ["dm", "target:111", "target:222"])


class ManifestAndScheduleTests(SetupApiTestCase):
    async def test_manifest_path_saved_only_when_valid(self):
        bad = self.root / "bad.yaml"
        bad.write_text("schema_version: 2\n")
        data = await self.call("POST", "/api/manifest/load", {"path": str(bad)})
        self.assertFalse(data["pathSaved"])
        self.assertNotIn("manifestPath", self.gw.config)
        data = await self.call("POST", "/api/manifest/load", {"path": str(EXAMPLE)})
        self.assertTrue(data["pathSaved"])
        self.assertEqual(self.gw.config["manifestPath"], str(EXAMPLE))

    async def test_each_sweep_rereads_the_manifest_file(self):
        path = self.root / "m.yaml"
        path.write_text(EXAMPLE.read_text())
        await self.call("POST", "/api/manifest/load", {"path": str(path)})
        # Drop the Meta partner and its connector from the file on disk.
        text = EXAMPLE.read_text()
        start = text.index("  meta:\n")
        end = text.index("  gam:\n")
        text = text[:start] + text[end:]
        c_start = text.index("  - id: meta-insights")
        c_end = text.index("  - id: gam-programmatic-direct")
        path.write_text(text[:c_start] + text[c_end:])
        report = await self.call("POST", "/api/sweep")
        self.assertNotIn("meta", {s["partner"] for s in report["sources"]})

    async def test_broken_manifest_file_keeps_last_valid(self):
        path = self.root / "m.yaml"
        path.write_text(EXAMPLE.read_text())
        await self.call("POST", "/api/manifest/load", {"path": str(path)})
        path.write_text("partners: [")
        report = await self.call("POST", "/api/sweep")
        self.assertIn("meta", {s["partner"] for s in report["sources"]})
        status = await self.call("GET", "/api/manifest")
        self.assertTrue(status["usingFallback"])

    async def test_schedule(self):
        data = await self.call("PUT", "/api/setup/schedule", {"sweepTime": "07:30", "timezone": "Europe/London"})
        self.assertEqual(data["schedule"]["sweepTime"], "07:30")
        self.assertEqual(data["schedule"]["digestEnabled"], False)  # default stays opt-in
        self.assertEqual(self.gw.config["schedule"], {"sweepTime": "07:30", "timezone": "Europe/London"})
        data = await self.call("PUT", "/api/setup/schedule", {"sweepTime": "7:30"}, expect=400)
        self.assertIn("HH:MM", data["errors"][0])

    async def test_setup_payload(self):
        data = await self.call("GET", "/api/setup")
        self.assertEqual(set(data), {"roles", "detection", "manifestPath", "manifest", "scheduler", "configError"})
        self.assertEqual(data["detection"]["errors"], [])
        self.assertEqual(data["scheduler"]["nextSweepAt"], "2026-10-03T12:00+00:00")  # due now (catch-up)


class GatewayDownTests(SetupApiTestCase):
    async def test_detection_errors_are_reported_not_raised(self):
        self.gw.fail_paths["/api/mcp"] = 500
        data = await self.call("POST", "/api/setup/detect")
        self.assertTrue(any("MCP servers" in e for e in data["detection"]["errors"]))

    async def test_binding_fails_cleanly_when_config_cannot_be_saved(self):
        self.gw.interfaces["slack"] = interface(True)
        self.gw.fail_paths["/api/apps/integration-radar/config"] = 500
        data = await self.call("PUT", "/api/setup/roles/chat",
                               {"candidate": "interface:slack", "destination": {"id": "dm"}}, expect=502)
        self.assertIn("could not save", data["error"])


if __name__ == "__main__":
    unittest.main()
