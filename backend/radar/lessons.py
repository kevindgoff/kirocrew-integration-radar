"""Saving dismissal lessons into Crew through the radar-courier agent.

Page apps and app backends cannot write Crew lessons directly (``POST
/api/lessons`` requires a session key the app SDK will not send). So dismissal
lessons wait in the store's outbox, and the courier, which runs in a session and
holds only ``send_message`` and ``learn_add``, saves them in one batched run.

The courier's word is not trusted: after it finishes, each lesson is checked
against ``GET /api/lessons`` and marked saved only if Crew actually has it.
Lesson rules are built from trusted fields only (see triage.lesson_rule).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .agents import COURIER, MAX_TASK_CHARS, AgentError, AgentRunner
from .change_store import ChangeStore
from .gateway import GatewayClient, GatewayError

log = logging.getLogger(__name__)

MAX_PER_RUN = 20


def courier_task(rules: list[str]) -> str:
    payload = json.dumps(rules, ensure_ascii=False, indent=1)
    return "\n".join([
        "Save lessons for Integration Radar. This is a delivery task: do not rewrite, merge or comment.",
        f"Call learn_add once for each of the {len(rules)} rule strings in the JSON array below,",
        'passing the string exactly as given as "rule", with "category": "knowledge" and "scope": "global".',
        "Do not call any other tool. When done, reply with the single word: done",
        "",
        payload,
    ])


def batches(rules: list[str]) -> list[list[str]]:
    """Split rules into courier tasks that each fit the spawn task limit."""
    out: list[list[str]] = []
    current: list[str] = []
    for rule in rules:
        if current and len(courier_task(current + [rule])) > MAX_TASK_CHARS:
            out.append(current)
            current = []
        current.append(rule)
    if current:
        out.append(current)
    return out


class LessonCourier:
    def __init__(self, store: ChangeStore, runner: AgentRunner, gateway: GatewayClient) -> None:
        self.store = store
        self.runner = runner
        self.gateway = gateway
        self._lock = asyncio.Lock()

    async def crew_rules(self) -> set[str]:
        data = await self.gateway.get("/api/lessons")
        lessons = data.get("lessons", []) if isinstance(data, dict) else []
        return {str(l.get("rule", "")).strip() for l in lessons if isinstance(l, dict)}

    async def deliver(self) -> dict[str, int]:
        """Save pending (and previously failed) lessons. Returns counts by outcome."""
        if self._lock.locked():
            return {"skipped": 1}
        async with self._lock:
            queue = (self.store.lessons("pending") + self.store.lessons("failed"))[:MAX_PER_RUN]
            if not queue:
                return {}
            # Some may already be in Crew (e.g. a run that finished after a restart).
            try:
                present = await self.crew_rules()
            except GatewayError as e:
                log.warning("could not read Crew lessons: %s", e)
                return {"error": 1}
            todo = []
            for lesson in queue:
                if lesson["rule"].strip() in present:
                    self.store.mark_lesson(lesson["changeId"], "saved")
                else:
                    todo.append(lesson)
            counts = {"saved": len(queue) - len(todo), "failed": 0}
            if not todo:
                return counts
            error = None
            for batch in batches([l["rule"] for l in todo]):
                try:
                    await self.runner.run(COURIER, courier_task(batch))
                except AgentError as e:
                    error = str(e)
            try:
                present = await self.crew_rules()
            except GatewayError as e:
                present, error = set(), error or str(e)
            for lesson in todo:
                if lesson["rule"].strip() in present:
                    self.store.mark_lesson(lesson["changeId"], "saved")
                    counts["saved"] += 1
                else:
                    self.store.mark_lesson(lesson["changeId"], "failed", error or "the courier did not save it")
                    counts["failed"] += 1
            return counts
