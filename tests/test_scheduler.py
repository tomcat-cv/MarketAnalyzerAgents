from datetime import datetime, timedelta, timezone
import unittest

from marketanalyzeragents.scheduler import ScheduleError, next_run_at, parse_schedule_time


class SchedulerTests(unittest.TestCase):
    def test_daily_schedule_rolls_to_tomorrow_when_time_has_passed(self) -> None:
        now = datetime(2026, 6, 5, 6, 30, tzinfo=timezone.utc)
        target = next_run_at(now, {"schedule": {"mode": "daily", "time": "06:00"}})
        self.assertEqual(target, datetime(2026, 6, 6, 6, 0, tzinfo=timezone.utc))

    def test_interval_schedule_uses_configured_minutes(self) -> None:
        now = datetime(2026, 6, 5, 6, 30, tzinfo=timezone.utc)
        target = next_run_at(now, {"schedule": {"mode": "interval", "interval_minutes": 90}})
        self.assertEqual(target, now + timedelta(minutes=90))

    def test_invalid_schedule_time_fails_loud(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_schedule_time("25:00")

if __name__ == "__main__":
    unittest.main()
