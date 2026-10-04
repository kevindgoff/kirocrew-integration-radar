"""Running this app's agents through the gateway's spawn API.

Backends cannot call models or most core tools themselves; they dispatch an
agent with ``POST /api/spawn`` and poll ``GET /api/spawn/{id}``. App agents are
registered as ``integration-radar/<name>`` in Crew's registry, but kiro-cli
dispatches agents by their declared ``name`` field, so that is what we spawn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .gateway import GatewayClient, GatewayError

log = logging.getLogger(__name__)

ANALYST = "change-analyst"
COURIER = "radar-courier"

#: Crew refuses spawn tasks longer than 5000 characters; stay safely under it.
MAX_TASK_CHARS = 4800
POLL_SECONDS = 3.0
DEFAULT_TIMEOUT_SECONDS = 300.0


class AgentError(Exception):
    """An agent run could not be started, failed, or timed out. Safe to show."""


@dataclass(frozen=True)
class AgentResult:
    id: str
    text: str


class AgentRunner:
    def __init__(self, gateway: GatewayClient, *, poll_seconds: float = POLL_SECONDS) -> None:
        self.gateway = gateway
        self.poll_seconds = poll_seconds

    async def run(self, agent: str, task: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> AgentResult:
        if len(task) > MAX_TASK_CHARS:
            raise AgentError(f"task for {agent} is {len(task)} characters; the limit is {MAX_TASK_CHARS}")
        body: dict[str, Any] = {
            "agent": agent,
            "task": task,
            # Runs are self-contained: no injected memory, lessons or project context
            # that could colour an analysis or a delivery.
            "include_memory": False,
            "include_lessons": False,
            "include_project": False,
            "silent": True,
            # These runs are unattended (after sweeps, on the digest timer), so they are
            # admitted without a human spawn approval. What that grants is bounded by
            # each agent's tools: change-analyst has none; radar-courier has only
            # send_message and learn_add.
            "approval_mode": "auto",
        }
        try:
            started = await self.gateway.post("/api/spawn", body)
        except GatewayError as e:
            raise AgentError(f"could not start {agent}: {e}") from None
        run_id = started.get("id") if isinstance(started, dict) else None
        if not run_id:
            raise AgentError(f"could not start {agent}: unexpected response {started!r}")

        deadline = time.monotonic() + timeout
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                status = await self.gateway.get(f"/api/spawn/{run_id}")
            except GatewayError as e:
                if time.monotonic() > deadline:
                    raise AgentError(f"{agent} run {run_id}: {e}") from None
                continue
            if isinstance(status, dict) and status.get("done"):
                if status.get("error"):
                    raise AgentError(f"{agent} run {run_id} failed: {status['error']}")
                return AgentResult(run_id, str(status.get("result") or ""))
            if isinstance(status, dict) and status.get("awaiting_approval"):
                raise AgentError(f"{agent} run {run_id} is waiting for approval in Crew")
            if time.monotonic() > deadline:
                raise AgentError(f"{agent} run {run_id} timed out after {int(timeout)}s")
