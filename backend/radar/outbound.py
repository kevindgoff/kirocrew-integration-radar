"""Outbound work in v0.1: urgent dashboard alerts and the weekly digest.

Urgent alerts go to the app's ``urgent`` notification channel in the Crew
dashboard. They fire only on a state change: when a matched, untriaged change
first becomes urgent (effective in under 30 days, or overdue), or when an urgent
change's effective date moves. The change id is the ``group_key``. Crew allows
a burst of 10 pushes and about one per 10 seconds after that, so a large batch
is sent as one combined alert, and a rate-limited push is simply retried on the
next evaluation.

The digest is rendered by the backend (see digest.py) and handed to
radar-courier only when the chat role is bound and healthy.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable

from .agents import COURIER, AgentError, AgentRunner
from .change_store import ChangeStore
from .digest import DASHBOARD_PATH, build_digest, courier_task, send_message_args
from .discovery import RoleState
from .gateway import GatewayClient, GatewayError
from .matcher import RankedChange

log = logging.getLogger(__name__)

#: More newly urgent changes than this in one evaluation become one combined alert.
MAX_SEPARATE_ALERTS = 5


def _alert_title(r: RankedChange, partner_name: str) -> str:
    c = r.change
    versions = f" {', '.join(c.versions)}" if c.versions else ""
    if r.days_left is None:
        when = ""
    elif r.days_left < 0:
        when = f", {-r.days_left}d overdue"
    elif r.days_left == 0:
        when = ", today"
    else:
        when = f", in {r.days_left}d"
    connectors = ", ".join(m.connector for m in r.matches)
    return f"{partner_name}{versions} {c.kind}{when}: affects {connectors}"[:500]


def _alert_body(r: RankedChange) -> str:
    # The dashboard is the only reader (no agent sees this), so the partner's title can appear.
    lines = [r.change.title, ""]
    lines += [f"{m.connector}: {m.confidence} ({'; '.join(m.reasons)}), action: {m.action}" for m in r.matches]
    return "\n".join(lines)[:20000]


class Outbound:
    def __init__(self, store: ChangeStore, gateway: GatewayClient, runner: AgentRunner) -> None:
        self.store = store
        self.gateway = gateway
        self.runner = runner

    # -- urgent alerts --------------------------------------------------------

    def newly_urgent(self, ranked: Iterable[RankedChange]) -> list[RankedChange]:
        alerted = self.store.alerted()
        out = []
        for r in ranked:
            if not r.urgent or r.change.status != "new":
                continue
            if r.change.id in alerted and alerted[r.change.id] == r.change.effective_at:
                continue  # already alerted for this date
            out.append(r)
        return out

    async def _push(self, payload: dict[str, Any]) -> bool:
        try:
            await self.gateway.post("/api/notifications/push", payload)
            return True
        except GatewayError as e:
            if e.status == 429:
                log.info("notification rate limit reached; remaining alerts wait for the next evaluation")
            else:
                log.warning("could not push an alert: %s", e)
            return False

    async def push_urgent(self, ranked: Iterable[RankedChange], partner_names: dict[str, str]) -> int:
        """Push alerts for changes that newly became urgent. Returns how many changes were alerted."""
        todo = self.newly_urgent(ranked)
        if not todo:
            return 0
        if len(todo) > MAX_SEPARATE_ALERTS:
            lines = [_alert_title(r, partner_names.get(r.change.partner, r.change.partner)) for r in todo]
            ok = await self._push({
                "channel": "urgent",
                "title": f"{len(todo)} partner API changes need attention within 30 days",
                "body": "\n".join(lines)[:20000],
                "group_key": "urgent-batch",
                "url": DASHBOARD_PATH,
            })
            if ok:
                for r in todo:
                    self.store.mark_alerted(r.change.id, r.change.effective_at)
            return len(todo) if ok else 0
        sent = 0
        for r in todo:
            ok = await self._push({
                "channel": "urgent",
                "title": _alert_title(r, partner_names.get(r.change.partner, r.change.partner)),
                "body": _alert_body(r),
                "group_key": r.change.id,
                "url": DASHBOARD_PATH,
            })
            if not ok:
                break  # rate limited or down: the rest go on the next evaluation
            self.store.mark_alerted(r.change.id, r.change.effective_at)
            sent += 1
        return sent

    # -- digest -----------------------------------------------------------------

    def render(self, ranked: list[RankedChange], partner_names: dict[str, str], today: date) -> str:
        assessed = {}
        for r in ranked:
            a = self.store.analysis(r.change.id)
            if a and a.get("status") == "done" and a.get("analysis"):
                assessed[r.change.id] = a["analysis"]["impact"]
        return build_digest(ranked, partner_names=partner_names, stale_partners=self.store.stale_partners(),
                            today=today, assessed=assessed).text

    async def send_digest(self, text: str, chat: RoleState, trigger: str) -> dict[str, Any]:
        """Deliver (or keep in the dashboard, or queue) one digest. Returns the stored record."""
        if chat.status == "bound":
            digest_id = self.store.add_digest(text, trigger, "sending", self._describe(chat))
            await self._deliver(digest_id, text, chat)
        elif chat.status == "degraded":
            digest_id = self.store.add_digest(text, trigger, "queued", self._describe(chat))
            self.store.update_digest(digest_id, "queued", error=chat.reason)
        else:
            # Unbound (off, proposed, or two tools and no choice yet): dashboard only.
            digest_id = self.store.add_digest(text, trigger, "dashboard")
            await self._push({"channel": "digest", "title": "Integration Radar weekly digest",
                              "body": text, "group_key": "digest", "url": DASHBOARD_PATH})
        return next(d for d in self.store.digests(limit=50) if d["id"] == digest_id)

    async def flush_queued(self, chat: RoleState) -> int:
        """Send digests queued while chat was degraded, once it is healthy again."""
        if chat.status != "bound":
            return 0
        queued = list(reversed(self.store.digests(limit=20, status="queued")))
        for d in queued:
            self.store.update_digest(d["id"], "sending")
            await self._deliver(d["id"], d["text"], chat)
        return len(queued)

    async def _deliver(self, digest_id: int, text: str, chat: RoleState) -> None:
        try:
            args = send_message_args(chat.binding or {})
            result = await self.runner.run(COURIER, courier_task(text, args))
        except (ValueError, AgentError) as e:
            self.store.update_digest(digest_id, "failed", error=str(e))
            return
        reply = result.text.strip().lower()
        if reply.startswith("failed"):
            self.store.update_digest(digest_id, "failed", error=result.text.strip()[:300])
        else:
            self.store.update_digest(digest_id, "delivered", destination=self._describe(chat))

    @staticmethod
    def _describe(chat: RoleState) -> str | None:
        b = chat.binding or {}
        dest = (b.get("destination") or {}).get("name")
        return f"{b.get('candidate')} → {dest}" if b.get("candidate") and dest else b.get("candidate")
