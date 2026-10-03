"""Runtime settings read from the environment the gateway spawns us with."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from . import APP_NAME

DEFAULT_GATEWAY_PORT = "5476"


@dataclass(frozen=True)
class Settings:
    app_name: str
    port: int
    home: Path
    proxy_secret: str
    gateway_origin: str

    @property
    def app_dir(self) -> Path:
        return self.home / "apps" / self.app_name

    @property
    def data_dir(self) -> Path:
        return self.app_dir / "data"

    @property
    def app_secret_path(self) -> Path:
        return self.app_dir / ".app_secret"

    @property
    def gateway_port(self) -> str:
        return self.gateway_origin.rsplit(":", 1)[-1].rstrip("/")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        home = env.get("KIROCREW_HOME") or str(Path.home() / ".kiro" / "crew")
        # The gateway does not always set KIROCREW_GATEWAY_ORIGIN; fall back to the
        # loopback gateway on KIROCREW_PORT (default 5476).
        origin = env.get("KIROCREW_GATEWAY_ORIGIN") or (
            f"http://127.0.0.1:{env.get('KIROCREW_PORT') or DEFAULT_GATEWAY_PORT}"
        )
        return cls(
            app_name=env.get("KIROCREW_APP_NAME") or APP_NAME,
            port=int(env.get("PORT") or 0),
            home=Path(home),
            proxy_secret=env.get("KIROCREW_PROXY_SECRET", ""),
            gateway_origin=origin.rstrip("/"),
        )
