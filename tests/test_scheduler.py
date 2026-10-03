import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.app_config import validate_schedule
from radar.scheduler import Scheduler, next_sweep_at, partners_due, sweep_due

UTC = timezone.utc
SCHEDULE = {"timezone": "UTC", "sweepTime": "06:00", "digestEnabled": False, "digestDay": "mon", "digestTime": "09:00"}


def at(h, m=0, day=3):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


class DueTests(unittest.TestCase):
    def test_due_after_sweep_time_once_per_day(self):
        self.assertFalse(sweep_due(SCHEDULE, at(5, 59), None))
        self.assertTrue(sweep_due(SCHEDULE, at(6, 0), None))
        self.assertTrue(sweep_due(SCHEDULE, at(23, 0), "2026-10-02"))  # catch-up later in the day
        self.assertFalse(sweep_due(SCHEDULE, at(23, 0), "2026-10-03"))

    def test_timezone(self):
        tz = {**SCHEDULE, "timezone": "America/New_York"}  # 06:00 EDT = 10:00 UTC
        self.assertFalse(sweep_due(tz, at(9, 59), None))
        self.assertTrue(sweep_due(tz, at(10, 0), None))
        # 02:00 UTC on the 4th is still the 3rd in New York.
        self.assertFalse(sweep_due(tz, at(2, 0, day=4), "2026-10-03"))

    def test_next_sweep_at(self):
        self.assertEqual(next_sweep_at(SCHEDULE, at(5), None), at(6))
        self.assertEqual(next_sweep_at(SCHEDULE, at(7), None), at(7))  # due now
        self.assertEqual(next_sweep_at(SCHEDULE, at(7), "2026-10-03"), at(6, day=4))

    def test_weekly_partners(self):
        now = at(6)
        poll = {"a": "daily", "b": "weekly", "c": "weekly", "d": "weekly"}
        last = {"b": (now - timedelta(days=2)).isoformat(), "c": (now - timedelta(days=6)).isoformat()}
        self.assertEqual(partners_due(poll, last, now), ["a", "c", "d"])


class ValidateScheduleTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_schedule({"sweepTime": "23:59", "timezone": "Europe/London", "digestDay": "fri",
                                            "digestEnabled": True}), [])

    def test_invalid(self):
        errors = validate_schedule({"sweepTime": "24:00", "digestTime": "9:00", "digestDay": "monday",
                                    "digestEnabled": "yes", "timezone": "Mars/Base", "extra": 1})
        self.assertEqual(len(errors), 6, errors)


class TickTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.now = at(5)
        self.config = {"schedule": {"sweepTime": "06:00"}}
        self.sweeps = 0
        self.fail = False

    def tearDown(self):
        self._tmp.cleanup()

    def scheduler(self):
        async def load_config():
            return self.config

        async def run_sweep():
            self.sweeps += 1
            if self.fail:
                raise RuntimeError("no valid connector manifest is loaded")

        return Scheduler(self.dir, load_config=load_config, run_sweep=run_sweep, now=lambda: self.now)

    async def test_runs_once_per_day(self):
        s = self.scheduler()
        self.assertFalse(await s.tick())
        self.now = at(6, 1)
        self.assertTrue(await s.tick())
        self.assertFalse(await s.tick())
        self.now = at(6, 1, day=4)
        self.assertTrue(await s.tick())
        self.assertEqual(self.sweeps, 2)

    async def test_restart_does_not_sweep_twice(self):
        self.now = at(7)
        await self.scheduler().tick()
        self.assertFalse(await self.scheduler().tick())  # a new Scheduler reads the persisted state
        self.assertEqual(self.sweeps, 1)

    async def test_failed_sweep_is_not_retried_every_tick(self):
        self.now, self.fail = at(7), True
        s = self.scheduler()
        self.assertTrue(await s.tick())
        self.assertFalse(await s.tick())
        status = await s.status()
        self.assertIn("no valid connector manifest", status["lastScheduledSweepError"])
        self.assertEqual(status["nextSweepAt"], "2026-10-04T06:00+00:00")

    async def test_config_failure_falls_back_to_default_schedule(self):
        async def broken():
            raise ConnectionError("gateway down")

        async def run():
            self.sweeps += 1

        s = Scheduler(self.dir, load_config=broken, run_sweep=run, now=lambda: at(7))
        self.assertTrue(await s.tick())
        self.assertEqual(self.sweeps, 1)


if __name__ == "__main__":
    unittest.main()
