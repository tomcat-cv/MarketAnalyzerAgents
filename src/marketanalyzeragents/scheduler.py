from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


class ScheduleError(ValueError):
    pass


def parse_schedule_time(value: str) -> time:
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ScheduleError("Schedule time must use HH:MM format.") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduleError("Schedule time must be between 00:00 and 23:59.")
    return time(hour=hour, minute=minute)


def local_now(timezone_name: str) -> datetime:
    if ZoneInfo is None:
        return datetime.now().astimezone()
    return datetime.now(ZoneInfo(timezone_name))


def next_run_at(now: datetime, settings: Mapping[str, Any]) -> datetime:
    schedule = settings.get("schedule", {})
    mode = str(schedule.get("mode", "daily")).strip().lower()
    if mode == "interval":
        interval_minutes = int(schedule.get("interval_minutes", 1440))
        if interval_minutes <= 0:
            raise ScheduleError("schedule.interval_minutes must be greater than zero.")
        return now + timedelta(minutes=interval_minutes)

    if mode != "daily":
        raise ScheduleError("schedule.mode must be daily or interval.")

    run_time = parse_schedule_time(str(schedule.get("time", "06:00")))
    candidate = now.replace(
        hour=run_time.hour,
        minute=run_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def seconds_until(target: datetime, now: datetime) -> float:
    return max(0.0, (target - now).total_seconds())
