"""Integration Radar backend entry point.

The gateway runs this file directly with its own interpreter, so this file's
directory is first on ``sys.path`` and the ``radar`` package imports as-is.
"""

from __future__ import annotations

import logging
import sys

from aiohttp import web

from radar.app import create_app
from radar.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not settings.port:
        sys.exit("PORT is not set; the gateway assigns it at spawn")
    if not settings.proxy_secret:
        logging.warning("KIROCREW_PROXY_SECRET is not set; every proxied request will be rejected")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    web.run_app(
        create_app(settings),
        host="127.0.0.1",
        port=settings.port,
        # Let a second copy die on bind so the gateway sees the port collision.
        reuse_address=not sys.platform.startswith("win"),
        print=None,
        access_log=None,
    )


if __name__ == "__main__":
    main()
