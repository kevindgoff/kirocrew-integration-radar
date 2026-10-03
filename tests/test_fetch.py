import asyncio
import unittest

from radar.fetch import FetchError, Fetcher, NotAllowedError, check_allowed

from fixture_fetcher import FixtureTransport, redirect

HOSTS = {"docs.alpha.example", "cdn.alpha.example"}


class CheckAllowedTests(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(check_allowed("https://Docs.Alpha.Example/x?y=1", HOSTS), "docs.alpha.example")

    def test_refusals(self):
        for url, why in (
            ("http://docs.alpha.example/x", "non-HTTPS"),
            ("https://evil.example/x", "outside the manifest allowlist"),
            ("https://docs.alpha.example.evil.example/x", "outside the manifest allowlist"),
            ("https://user:pw@docs.alpha.example/x", "credentials"),
            ("ftp://docs.alpha.example/x", "non-HTTPS"),
            ("https:///x", "outside the manifest allowlist"),
        ):
            with self.assertRaises(NotAllowedError, msg=url) as cm:
                check_allowed(url, HOSTS)
            self.assertIn(why, str(cm.exception))


class FetcherTests(unittest.IsolatedAsyncioTestCase):
    def fetcher(self, routes):
        self.transport = FixtureTransport(routes)
        return Fetcher(HOSTS, raw_request=self.transport)

    async def test_plain_fetch(self):
        f = self.fetcher({"https://docs.alpha.example/a": (200, {"Content-Type": "text/plain; charset=latin-1"}, "caf\xe9".encode("latin-1"))})
        r = await f.fetch("https://docs.alpha.example/a")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.text(), "caf\xe9")

    async def test_follows_allowed_redirects(self):
        f = self.fetcher({
            "https://docs.alpha.example/a": redirect("/b"),
            "https://docs.alpha.example/b": redirect("https://cdn.alpha.example/c", 302),
            "https://cdn.alpha.example/c": (200, {"content-type": "text/plain"}, b"ok"),
        })
        r = await f.fetch("https://docs.alpha.example/a")
        self.assertEqual(r.final_url, "https://cdn.alpha.example/c")
        self.assertEqual(r.body, b"ok")

    async def test_redirect_to_http_is_refused_before_request(self):
        f = self.fetcher({"https://docs.alpha.example/feed": redirect("http://docs.alpha.example/feed", 302)})
        with self.assertRaises(NotAllowedError):
            await f.fetch("https://docs.alpha.example/feed")
        self.assertEqual(self.transport.requested, ["https://docs.alpha.example/feed"])

    async def test_redirect_off_allowlist_is_refused_before_request(self):
        f = self.fetcher({"https://docs.alpha.example/a": redirect("https://tracker.example/x")})
        with self.assertRaises(NotAllowedError):
            await f.fetch("https://docs.alpha.example/a")
        self.assertEqual(self.transport.requested, ["https://docs.alpha.example/a"])

    async def test_disallowed_start_url_makes_no_request(self):
        f = self.fetcher({})
        with self.assertRaises(NotAllowedError):
            await f.fetch("https://evil.example/")
        self.assertEqual(self.transport.requested, [])

    async def test_redirect_loop(self):
        f = self.fetcher({"https://docs.alpha.example/a": redirect("/a")})
        with self.assertRaises(FetchError) as cm:
            await f.fetch("https://docs.alpha.example/a")
        self.assertIn("too many redirects", str(cm.exception))

    async def test_http_error_and_missing_location(self):
        f = self.fetcher({
            "https://docs.alpha.example/500": (500, {}, b""),
            "https://docs.alpha.example/r": (302, {}, b""),
        })
        with self.assertRaisesRegex(FetchError, "HTTP 500"):
            await f.fetch("https://docs.alpha.example/500")
        with self.assertRaisesRegex(FetchError, "Location"):
            await f.fetch("https://docs.alpha.example/r")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    """The real aiohttp transport, against a local server streaming slowly."""

    async def asyncSetUp(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer

        async def slow(request):
            resp = web.StreamResponse(headers={"Content-Type": "text/plain"})
            await resp.prepare(request)
            for i in range(20):
                await resp.write(b"x" * 10_000)
                await asyncio.sleep(0.005)
            await resp.write_eof()
            return resp

        app = web.Application()
        app.router.add_get("/slow", slow)
        app.router.add_get("/moved", lambda r: web.HTTPFound("/slow"))
        self.server = TestServer(app)
        await self.server.start_server()
        self.fetcher = Fetcher(HOSTS)

    async def asyncTearDown(self):
        await self.fetcher.close()
        await self.server.close()

    async def test_reads_the_whole_streamed_body(self):
        status, _, body = await self.fetcher._request(str(self.server.make_url("/slow")))
        self.assertEqual((status, len(body)), (200, 200_000))

    async def test_does_not_follow_redirects_itself(self):
        status, headers, _ = await self.fetcher._request(str(self.server.make_url("/moved")))
        self.assertEqual((status, headers["location"]), (302, "/slow"))

    async def test_size_cap(self):
        import radar.fetch as fetch_module
        original = fetch_module.MAX_BYTES
        fetch_module.MAX_BYTES = 50_000
        try:
            with self.assertRaisesRegex(FetchError, "exceeds"):
                await self.fetcher._request(str(self.server.make_url("/slow")))
        finally:
            fetch_module.MAX_BYTES = original


if __name__ == "__main__":
    unittest.main()
