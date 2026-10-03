import tempfile
import unittest
from pathlib import Path

from radar.gateway import GatewayClient, GatewayError
from radar.settings import Settings

from fake_gateway import FakeGateway


class GatewayClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.gw = FakeGateway()
        await self.gw.start()
        self.settings = self.gw.settings(Path(self._tmp.name))
        self.client = GatewayClient(self.settings)

    async def asyncTearDown(self):
        await self.client.close()
        await self.gw.close()
        self._tmp.cleanup()

    async def test_exchanges_secret_and_sends_cookie(self):
        self.assertEqual(await self.client.get("/api/slack/channels"), [{"id": "dm", "name": "Direct Message"}])
        self.assertEqual(self.gw.count("POST", "/api/apps/integration-radar/token"), 1)
        await self.client.get("/api/mcp")
        self.assertEqual(self.gw.count("POST", "/api/apps/integration-radar/token"), 1)  # token reused

    async def test_reexchanges_once_on_403(self):
        await self.client.get("/api/mcp")
        self.gw.revoke_tokens()
        self.assertIsInstance(await self.client.get("/api/mcp"), list)
        self.assertEqual(self.gw.count("POST", "/api/apps/integration-radar/token"), 2)

    async def test_rereads_rotated_secret(self):
        await self.client.get("/api/mcp")
        self.gw.revoke_tokens()
        self.gw.secret = "rotated"
        self.settings.app_secret_path.write_text("rotated")
        self.assertIsInstance(await self.client.get("/api/mcp"), list)

    async def test_persistent_refusal_raises(self):
        self.gw.fail_paths["/api/mcp"] = 403
        with self.assertRaises(GatewayError) as cm:
            await self.client.get("/api/mcp")
        self.assertEqual(cm.exception.status, 403)

    async def test_http_errors_raise_with_status(self):
        self.gw.fail_paths["/api/teams/config"] = 404
        with self.assertRaises(GatewayError) as cm:
            await self.client.get("/api/teams/config")
        self.assertEqual(cm.exception.status, 404)

    async def test_put_roundtrip(self):
        await self.client.put("/api/apps/integration-radar/config", {"a": 1})
        self.assertEqual(await self.client.get("/api/apps/integration-radar/config"), {"a": 1})

    async def test_missing_secret(self):
        self.settings.app_secret_path.unlink()
        with self.assertRaisesRegex(GatewayError, "app secret is missing"):
            await self.client.get("/api/mcp")

    async def test_unreachable_gateway(self):
        client = GatewayClient(Settings("integration-radar", 0, self.settings.home, "s", "http://127.0.0.1:9"))
        try:
            with self.assertRaisesRegex(GatewayError, "unreachable"):
                await client.get("/api/mcp")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
