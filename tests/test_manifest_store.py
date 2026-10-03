import json
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from radar.app import create_app
from radar.manifest_store import ManifestStore
from radar.proxy_auth import HEADER, sign
from radar.settings import Settings
from test_manifest import BASE

SECRET = "test-secret"


class ManifestStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.path = self.root / "manifest.yaml"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text):
        self.path.write_text(text)
        return str(self.path)

    def test_loads_valid_manifest(self):
        store = ManifestStore(self.data_dir)
        self.assertIsNone(store.current)
        result = store.load_path(self.write(BASE))
        self.assertTrue(result.ok)
        self.assertEqual(store.current.product, "sample-product")
        status = store.status()
        self.assertEqual(status["inUse"]["path"], str(self.path))
        self.assertFalse(status["usingFallback"])

    def test_invalid_manifest_keeps_last_valid(self):
        store = ManifestStore(self.data_dir)
        store.load_path(self.write(BASE))
        result = store.load_path(self.write(BASE.replace("partner: alpha", "partner: gamma")))
        self.assertFalse(result.ok)
        self.assertEqual(store.current.product, "sample-product")
        status = store.status()
        self.assertTrue(status["usingFallback"])
        self.assertFalse(status["lastAttempt"]["ok"])
        self.assertEqual(status["lastAttempt"]["errors"][0]["line"], 19)

    def test_missing_file_keeps_last_valid(self):
        store = ManifestStore(self.data_dir)
        store.load_path(self.write(BASE))
        self.path.unlink()
        result = store.load_path(str(self.path))
        self.assertFalse(result.ok)
        self.assertIn("no manifest file", result.errors[0].message)
        self.assertIsNotNone(store.current)

    def test_last_valid_survives_restart(self):
        ManifestStore(self.data_dir).load_path(self.write(BASE))
        self.write("not: [valid")
        restarted = ManifestStore(self.data_dir)
        self.assertEqual(restarted.current.product, "sample-product")
        self.assertEqual(restarted.current_path, str(self.path))

    def test_invalid_first_load_persists_nothing(self):
        store = ManifestStore(self.data_dir)
        store.load_path(self.write("schema_version: 2\n"))
        self.assertIsNone(store.current)
        self.assertIsNone(ManifestStore(self.data_dir).current)

    def test_path_checks(self):
        store = ManifestStore(self.data_dir)
        self.assertIn("absolute", store.load_path("manifest.yaml").errors[0].message)
        self.assertIn("absolute", store.load_path("").errors[0].message)
        other = self.root / "manifest.txt"
        other.write_text(BASE)
        self.assertIn(".yaml or .yml", store.load_path(str(other)).errors[0].message)

    def test_corrupt_state_file_is_ignored(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "manifest-state.json").write_text("{nope")
        self.assertIsNone(ManifestStore(self.data_dir).current)


def signed(method, target, body=b""):
    ts = int(time.time())
    return {HEADER: f"{ts}:{sign(SECRET, ts, method, target, body)}", "Content-Type": "application/json"}


class ManifestApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.manifest_path = root / "m.yaml"
        self.manifest_path.write_text(BASE)
        settings = Settings("integration-radar", 0, root / "home", SECRET, "http://127.0.0.1:5476")
        self.client = TestClient(TestServer(create_app(settings)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    async def post(self, path, payload):
        body = json.dumps(payload).encode()
        return await self.client.post(path, data=body, headers=signed("POST", path, body))

    async def test_status_starts_empty(self):
        resp = await self.client.get("/api/manifest", headers=signed("GET", "/api/manifest"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), {"inUse": None, "lastAttempt": None, "usingFallback": False})

    async def test_load_then_status(self):
        resp = await self.post("/api/manifest/load", {"path": str(self.manifest_path)})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["lastAttempt"]["ok"])
        self.assertEqual(data["inUse"]["allowedHosts"], ["beta.example", "docs.alpha.example"])

    async def test_validate_is_a_dry_run(self):
        resp = await self.post("/api/manifest/validate", {"text": BASE.replace("https://", "http://", 1)})
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["line"], 9)
        status = await (await self.client.get("/api/manifest", headers=signed("GET", "/api/manifest"))).json()
        self.assertIsNone(status["inUse"])

    async def test_bad_bodies_are_400(self):
        self.assertEqual((await self.post("/api/manifest/load", {"path": 3})).status, 400)
        self.assertEqual((await self.post("/api/manifest/validate", ["x"])).status, 400)
        resp = await self.client.post(
            "/api/manifest/validate", data=b"nope", headers=signed("POST", "/api/manifest/validate", b"nope")
        )
        self.assertEqual(resp.status, 400)

    async def test_unsigned_manifest_calls_are_401(self):
        self.assertEqual((await self.client.get("/api/manifest")).status, 401)


if __name__ == "__main__":
    unittest.main()
