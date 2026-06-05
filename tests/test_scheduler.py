from datetime import datetime, timedelta, timezone
import unittest

from dailyresearch.scheduler import ScheduleError, next_run_at, parse_schedule_time, seconds_until


class SchedulerTests(unittest.TestCase):
    def test_daily_schedule_uses_same_day_when_time_is_future(self) -> None:
        now = datetime(2026, 6, 5, 5, 30, tzinfo=timezone.utc)
        target = next_run_at(now, {"schedule": {"mode": "daily", "time": "06:00"}})
        self.assertEqual(target, datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc))

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

    def test_seconds_until_never_returns_negative_values(self) -> None:
        now = datetime(2026, 6, 5, 6, 30, tzinfo=timezone.utc)
        self.assertEqual(seconds_until(now - timedelta(minutes=1), now), 0.0)


if __name__ == "__main__":
    unittest.main()
