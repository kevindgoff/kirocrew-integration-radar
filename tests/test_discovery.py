import unittest

from radar.discovery import build_detection, classify_mcp, resolve_role, resolve_roles

from fake_gateway import ATLASSIAN_TOOLS, interface, mcp_server

NO_INTERFACES: dict = {}


def detection(servers=(), interfaces=NO_INTERFACES):
    return build_detection(list(servers), dict(interfaces))


def ids(candidates):
    return [c.id for c in candidates]


class ClassifyTests(unittest.TestCase):
    def test_one_server_can_fill_several_roles(self):
        roles = classify_mcp([mcp_server("atlassian-rovo-mcp", ATLASSIAN_TOOLS)])
        self.assertEqual(ids(roles["tracker"]), ["mcp:atlassian-rovo-mcp"])
        self.assertEqual(ids(roles["wiki"]), ["mcp:atlassian-rovo-mcp"])
        self.assertEqual(ids(roles["repo"]), ["mcp:atlassian-rovo-mcp"])
        self.assertEqual(roles["chat"], [])  # "getJsmOpsTeamInfo" is not "teams"
        self.assertTrue(roles["tracker"][0].evidence)
        self.assertTrue(all("Jira" in t or "Issue" in t for t in roles["tracker"][0].evidence))
        self.assertTrue(all(t.startswith("bitbucket") for t in roles["repo"][0].evidence))

    def test_crew_internal_servers_are_not_candidates(self):
        roles = classify_mcp([mcp_server("kirocrew-core", ["send_message", "create_issue"])])
        self.assertTrue(all(not v for v in roles.values()))

    def test_snake_case_and_server_names(self):
        roles = classify_mcp([
            mcp_server("github", ["create_issue", "create_pull_request", "get_file_contents"]),
            mcp_server("linear", []),  # no tools reported, but the name says tracker
            mcp_server("notes", ["list_notes"]),
        ])
        self.assertEqual(ids(roles["tracker"]), ["mcp:github", "mcp:linear"])
        self.assertEqual(ids(roles["repo"]), ["mcp:github"])
        self.assertEqual(roles["wiki"], [])

    def test_unusable_servers_are_listed_but_not_usable(self):
        roles = classify_mcp([
            mcp_server("jira-auth", ["getJiraIssue"], status="needs_auth"),
            mcp_server("jira-off", ["getJiraIssue"], enabled=False),
        ])
        statuses = {c.id: (c.usable, c.status) for c in roles["tracker"]}
        self.assertEqual(statuses, {"mcp:jira-auth": (False, "needs_auth"), "mcp:jira-off": (False, "disabled")})


class ChatTests(unittest.TestCase):
    def test_only_connected_or_configured_interfaces(self):
        d = detection(interfaces={"slack": interface(True), "teams": interface(False), "discord": interface(False, True)})
        self.assertEqual(ids(d.candidates["chat"]), ["interface:slack", "interface:discord"])
        self.assertEqual([c.usable for c in d.candidates["chat"]], [True, False])

    def test_interface_preferred_over_mcp_for_same_tool(self):
        d = detection([mcp_server("slack-mcp", ["slack_post_message"])], {"slack": interface(True)})
        self.assertEqual(ids(d.candidates["chat"]), ["interface:slack"])

    def test_mcp_chat_is_listed_but_cannot_deliver(self):
        d = detection([mcp_server("slack-mcp", ["slack_post_message"])])
        (c,) = d.candidates["chat"]
        self.assertFalse(c.deliverable)
        self.assertEqual(resolve_role("chat", d, None).status, "off")


class ResolutionTests(unittest.TestCase):
    """The P0 acceptance criteria for discovery and role binding."""

    def test_only_slack_connected_is_proposed_for_chat(self):
        d = detection(interfaces={"slack": interface(True)})
        state = resolve_role("chat", d, None)
        self.assertEqual((state.status, state.proposal), ("proposed", "interface:slack"))

    def test_slack_and_teams_means_the_user_must_choose(self):
        d = detection(interfaces={"slack": interface(True), "teams": interface(True)})
        state = resolve_role("chat", d, None)
        self.assertEqual(state.status, "needs_choice")
        self.assertIsNone(state.proposal)

    def test_no_tracker_is_off(self):
        roles = resolve_roles(detection(), {})
        self.assertEqual({r: s.status for r, s in roles.items()},
                         {"tracker": "off", "wiki": "off", "chat": "off", "repo": "off"})
        self.assertIn("dashboard", roles["tracker"].reason)

    def test_unusable_candidates_do_not_count(self):
        d = detection([mcp_server("a", ["getJiraIssue"]), mcp_server("b", ["getJiraIssue"], status="needs_auth")])
        self.assertEqual(resolve_role("tracker", d, None).status, "proposed")

    def test_bound(self):
        d = detection(interfaces={"slack": interface(True), "teams": interface(True)})
        state = resolve_role("chat", d, {"candidate": "interface:teams", "destination": {"id": "dm"}})
        self.assertEqual(state.status, "bound")

    def test_vanished_binding_is_degraded(self):
        state = resolve_role("tracker", detection(), {"candidate": "mcp:gone"})
        self.assertEqual(state.status, "degraded")
        self.assertIn("queued", state.reason)

    def test_disconnected_binding_is_degraded(self):
        d = detection(interfaces={"slack": interface(False, True)})
        state = resolve_role("chat", d, {"candidate": "interface:slack", "destination": {"id": "dm"}})
        self.assertEqual(state.status, "degraded")

    def test_switched_off_stays_off(self):
        d = detection(interfaces={"slack": interface(True)})
        state = resolve_role("chat", d, {"off": True})
        self.assertEqual((state.status, state.reason), ("off", "switched off in Setup"))


if __name__ == "__main__":
    unittest.main()
