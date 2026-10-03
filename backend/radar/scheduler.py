"""In-backend scheduler for deterministic work (not Crew crons, which prompt agents).

Each tick asks one question per job: is it due? A daily sweep is due when the
local time (in the configured timezone) is past ``sweepTime`` and today's
scheduled sweep has not run. That one rule also catches up after downtime: a
backend started at 11:00 with a 06:00 sweep time sweeps once, right away. The
last run date is persisted so a restart never sweeps twice in a day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .app_config import schedule_of

log = logging.getLogger(__name__)

STATE_FILE = "scheduler-state.json"
TICK_SECONDS = 60
#: The backend can start while the gateway is still coming up; let it settle first.
STARTUP_DELAY_SECONDS = 30
WEEKLY_POLL_DAYS = 6  # a weekly partner is due when its last success is older than this


def _parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def sweep_due(schedule: dict[str, Any], now: datetime, last_run_date: str | None) -> bool:
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    h, m = _parse_hhmm(schedule["sweepTime"])
    return (local.hour, local.minute) >= (h, m) and last_run_date != local.date().isoformat()


def next_sweep_at(schedule: dict[str, Any], now: datetime, last_run_date: str | None) -> datetime:
    tz = ZoneInfo(schedule["timezone"])
    local = now.astimezone(tz)
    h, m = _parse_hhmm(schedule["sweepTime"])
    if sweep_due(schedule, now, last_run_date):
        return now
    today_slot = local.replace(hour=h, minute=m, second=0, microsecond=0)
    slot = today_slot if today_slot > local and last_run_date != local.date().isoformat() else today_slot + timedelta(days=1)
    return slot.astimezone(timezone.utc)


def partners_due(poll: dict[str, str], last_success: dict[str, str | None], now: datetime) -> list[str]:
    """Daily partners always; weekly partners when their last success is old enough (or never)."""
    due = []
    for partner, cadence in poll.items():
        if cadence != "weekly":
            due.append(partner)
            continue
        last = last_success.get(partner)
        if last is None or datetime.fromisoformat(last) <= now - timedelta(days=WEEKLY_POLL_DAYS):
            due.append(partner)
    return due


class Scheduler:
    def __init__(
        self,
        data_dir: Path,
        *,
        load_config: Callable[[], Awaitable[dict[str, Any]]],
        run_sweep: Callable[[], Awaitable[Any]],
        now: Callable[[], datetime],
    ) -> None:
        self._state_path = data_dir / STATE_FILE
        self._load_config = load_config
        self._run_sweep = run_sweep
        self._now = now
        self._task: asyncio.Task | None = None
        self.state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError as e:
            log.error("could not persist scheduler state: %s", e)

    async def schedule(self) -> dict[str, Any]:
        try:
            return schedule_of(await self._load_config())
        except Exception as e:  # gateway down: keep sweeping on the default schedule
            log.warning("using the default schedule: %s", e)
            return schedule_of({})

    async def tick(self) -> bool:
        """Run whatever is due. Returns True if a sweep ran."""
        schedule = await self.schedule()
        now = self._now()
        if not sweep_due(schedule, now, self.state.get("lastScheduledSweep")):
            return False
        local_date = now.astimezone(ZoneInfo(schedule["timezone"])).date().isoformat()
        # Record first: a sweep that fails (e.g. no manifest yet) must not retry every tick.
        self.state["lastScheduledSweep"] = local_date
        self._write_state()
        try:
            await self._run_sweep()
        except Exception as e:
            log.warning("scheduled sweep did not run: %s", e)
            self.state["lastScheduledSweepError"] = str(e)
        else:
            self.state.pop("lastScheduledSweepError", None)
        self._write_state()
        return True

    async def status(self) -> dict[str, Any]:
        schedule = await self.schedule()
        last = self.state.get("lastScheduledSweep")
        return {
            "schedule": schedule,
            "lastScheduledSweep": last,
            "lastScheduledSweepError": self.state.get("lastScheduledSweepError"),
            "nextSweepAt": next_sweep_at(schedule, self._now(), last).isoformat(timespec="minutes"),
        }

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="radar-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)
