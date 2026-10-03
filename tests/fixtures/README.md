# Recorded fixtures

Real responses from public partner documentation, recorded on 2026-10-03 and trimmed to
the parts the adapters read. Tests serve them through `tests/fixture_fetcher.py`. No test
touches the live network.

| File | Recorded from | Trimmed to |
| --- | --- | --- |
| `gam-api-schedule.csv` | `https://developers.google.com/ad-manager/downloads/api-schedule.csv` | unchanged |
| `gam-deprecation.html` | `https://developers.google.com/ad-manager/api/deprecation` | the `<article>` element, scripts and SVGs removed |
| `google-ads-sunset-dates.html` | `https://developers.google.com/google-ads/api/docs/sunset-dates` | the `<article>` element, scripts and SVGs removed |
| `meta-versions.html` | `https://developers.facebook.com/docs/marketing-api/marketing-api-changelog/versions` (301 to `/documentation/ads-commerce/...`) | the one `<script type="application/json">` payload that holds the version table |
| `ads-developer-blog.atom.xml` | `https://feeds.feedburner.com/GoogleAdsDeveloperBlog` | the feed header and the first four entries |

Tests that depend on dates pin the clock to 2026-10-03, so they keep passing as the
recorded schedules age.

To refresh a fixture, fetch the URL again, trim it the same way, update the recording
date above, and adjust any assertions about specific versions or dates.
