import time
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from radar.app import create_app
from radar.proxy_auth import HEADER, sign, verify
from radar.settings import Settings

SECRET = "test-secret"


def make_settings(secret: str = SECRET) -> Settings:
    return Settings(
        app_name="integration-radar",
        port=0,
        home=Path("/nonexistent"),
        proxy_secret=secret,
        gateway_origin="http://127.0.0.1:5476",
    )


def header(method: str, target: str, body: bytes = b"", secret: str = SECRET, ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    return {HEADER: f"{ts}:{sign(secret, ts, method, target, body)}"}


class VerifyTests(unittest.TestCase):
    def test_valid_signature(self):
        ts = 1_700_000_000
        sig = sign(SECRET, ts, "GET", "/api/x", b"")
        self.assertTrue(verify(f"{ts}:{sig}", secret=SECRET, method="GET", target="/api/x", body=b"", now=ts))

    def test_rejects_skew_beyond_60s(self):
        ts = 1_700_000_000
        sig = sign(SECRET, ts, "GET", "/api/x", b"")
        self.assertTrue(verify(f"{ts}:{sig}", secret=SECRET, method="GET", target="/api/x", body=b"", now=ts + 60))
        self.assertFalse(verify(f"{ts}:{sig}", secret=SECRET, method="GET", target="/api/x", body=b"", now=ts + 61))
        self.assertFalse(verify(f"{ts}:{sig}", secret=SECRET, method="GET", target="/api/x", body=b"", now=ts - 61))

    def test_rejects_malformed_headers(self):
        for value in (None, "", "nocolon", ":abc", "abc:def", "123:"):
            self.assertFalse(verify(value, secret=SECRET, method="GET", target="/api/x", body=b""), value)

    def test_rejects_when_secret_missing(self):
        ts = int(time.time())
        sig = sign("", ts, "GET", "/api/x", b"")
        self.assertFalse(verify(f"{ts}:{sig}", secret="", method="GET", target="/api/x", body=b""))

    def test_signature_binds_method_target_and_body(self):
        ts = int(time.time())
        sig = sign(SECRET, ts, "POST", "/api/x", b"{}")
        h = f"{ts}:{sig}"
        self.assertTrue(verify(h, secret=SECRET, method="POST", target="/api/x", body=b"{}"))
        self.assertFalse(verify(h, secret=SECRET, method="PUT", target="/api/x", body=b"{}"))
        self.assertFalse(verify(h, secret=SECRET, method="POST", target="/api/y", body=b"{}"))
        self.assertFalse(verify(h, secret=SECRET, method="POST", target="/api/x", body=b"{ }"))


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = create_app(make_settings())

        async def echo(request: web.Request) -> web.Response:
            return web.json_response({"body": (await request.read()).decode(), "q": dict(request.query)})

        app.router.add_post("/api/echo", echo)
        app.router.add_get("/api/echo", echo)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_health_is_unsigned(self):
        resp = await self.client.get("/health")
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), {"status": "ok"})

    async def test_unsigned_api_request_is_401(self):
        resp = await self.client.get("/api/status")
        self.assertEqual(resp.status, 401)

    async def test_signed_api_request_is_200(self):
        resp = await self.client.get("/api/status", headers=header("GET", "/api/status"))
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["app"], "integration-radar")

    async def test_unknown_path_still_requires_signature(self):
        resp = await self.client.get("/api/does-not-exist")
        self.assertEqual(resp.status, 401)

    async def test_wrong_secret_is_401(self):
        resp = await self.client.get("/api/status", headers=header("GET", "/api/status", secret="other"))
        self.assertEqual(resp.status, 401)

    async def test_stale_timestamp_is_401(self):
        stale = int(time.time()) - 120
        resp = await self.client.get("/api/status", headers=header("GET", "/api/status", ts=stale))
        self.assertEqual(resp.status, 401)

    async def test_post_body_is_signed_and_still_readable(self):
        body = b'{"a": 1}'
        resp = await self.client.post("/api/echo", data=body, headers=header("POST", "/api/echo", body))
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["body"], body.decode())

    async def test_tampered_body_is_401(self):
        hdrs = header("POST", "/api/echo", b'{"a": 1}')
        resp = await self.client.post("/api/echo", data=b'{"a": 2}', headers=hdrs)
        self.assertEqual(resp.status, 401)

    async def test_signs_over_raw_encoded_query(self):
        target = "/api/echo?q=a%20b%2Bc"
        resp = await self.client.get(target, headers=header("GET", target))
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["q"], {"q": "a b+c"})

    async def test_signature_over_decoded_query_is_rejected(self):
        resp = await self.client.get("/api/echo?q=a%20b", headers=header("GET", "/api/echo?q=a b"))
        self.assertEqual(resp.status, 401)

    async def test_health_post_is_not_exempt(self):
        resp = await self.client.post("/health")
        self.assertEqual(resp.status, 401)


class NoSecretTests(unittest.IsolatedAsyncioTestCase):
    async def test_fails_closed_without_secret(self):
        client = TestClient(TestServer(create_app(make_settings(secret=""))))
        await client.start_server()
        try:
            resp = await client.get("/api/status", headers=header("GET", "/api/status", secret=""))
            self.assertEqual(resp.status, 401)
            self.assertEqual((await client.get("/health")).status, 200)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
