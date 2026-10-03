"""Recorded-fixture transport for Fetcher: no live network in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from radar.fetch import Fetcher

FIXTURES = Path(__file__).resolve().parent / "fixtures"

GAM_CSV = "https://developers.google.com/ad-manager/downloads/api-schedule.csv"
GAM_DEPRECATION = "https://developers.google.com/ad-manager/api/deprecation"
GADS_SUNSET = "https://developers.google.com/google-ads/api/docs/sunset-dates"
GADS_FEED = "https://feeds.feedburner.com/GoogleAdsDeveloperBlog"
META_VERSIONS = "https://developers.facebook.com/docs/marketing-api/marketing-api-changelog/versions"
# Recorded behaviour: the docs path redirects to a new path on the same host.
META_VERSIONS_FINAL = (
    "https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/versions"
)

Response = tuple[int, Mapping[str, str], bytes]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def ok(name: str, content_type: str = "text/html; charset=utf-8") -> Response:
    return 200, {"content-type": content_type}, fixture(name)


def redirect(location: str, status: int = 301) -> Response:
    return status, {"location": location}, b""


def recorded_routes() -> dict[str, Response]:
    return {
        GAM_CSV: ok("gam-api-schedule.csv", "text/csv"),
        GAM_DEPRECATION: ok("gam-deprecation.html"),
        GADS_SUNSET: ok("google-ads-sunset-dates.html"),
        GADS_FEED: ok("ads-developer-blog.atom.xml", "text/xml; charset=utf-8"),
        META_VERSIONS: redirect(META_VERSIONS_FINAL),
        META_VERSIONS_FINAL: ok("meta-versions.html", 'text/html; charset="utf-8"'),
    }


class FixtureTransport:
    """Serves canned responses by URL and records every URL requested."""

    def __init__(self, routes: dict[str, Response] | None = None) -> None:
        self.routes = recorded_routes() if routes is None else routes
        self.requested: list[str] = []

    async def __call__(self, url: str) -> Response:
        self.requested.append(url)
        if url not in self.routes:
            return 404, {"content-type": "text/plain"}, b"not found"
        return self.routes[url]

    def factory(self):
        return lambda hosts: Fetcher(hosts, raw_request=self)
