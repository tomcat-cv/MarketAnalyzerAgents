from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketStatus:
    market: str
    state: str
    as_of_beijing: datetime
    session_open_beijing: datetime | None
    session_close_beijing: datetime | None


@dataclass(frozen=True)
class TradingCalendar:
    source: str
    closed_dates: frozenset[str]
    open_dates: frozenset[str]
    early_closes: Mapping[str, str]
    strict: bool = False

    def is_trading_day(self, day: date) -> bool:
        text = day.isoformat()
        if text in self.closed_dates:
            return False
        if text in self.open_dates:
            return True
        if self.strict and self.open_dates:
            return False
        return day.weekday() < 5

    def early_close(self, day: date) -> str | None:
        return self.early_closes.get(day.isoformat())


def load_trading_calendar(path: Path, *, strict: bool = False) -> TradingCalendar:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("calendar file must contain a JSON object")
    open_dates = payload.get("trading_days", payload.get("open_dates", []))
    closed_dates = payload.get("closed_dates", payload.get("holidays", []))
    early_closes = payload.get("early_closes", {})
    if not isinstance(open_dates, list) or not isinstance(closed_dates, list):
        raise ValueError("calendar open/closed dates must be lists")
    if not isinstance(early_closes, Mapping):
        raise ValueError("calendar early_closes must be an object")
    return TradingCalendar(
        source=str(payload.get("source", path)),
        open_dates=frozenset(str(value) for value in open_dates),
        closed_dates=frozenset(str(value) for value in closed_dates),
        early_closes={str(key): str(value) for key, value in early_closes.items()},
        strict=strict,
    )


def calendar_from_settings(
    market_settings: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> TradingCalendar | None:
    config = market_settings.get("calendar", {})
    if not isinstance(config, Mapping):
        return None
    provider = str(config.get("provider", "config")).strip()
    if provider in {"", "config"}:
        return None
    if provider != "file":
        raise ValueError("calendar.provider must be config or file")
    raw_path = str(config.get("path", "")).strip()
    if not raw_path:
        raise ValueError("calendar.path is required when calendar.provider=file")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        if root is None:
            raise ValueError("root is required for relative calendar.path")
        path = root / path
    if not path.exists():
        if bool(config.get("strict", False)):
            raise FileNotFoundError(f"calendar file not found: {path}")
        return None
    return load_trading_calendar(path, strict=bool(config.get("strict", False)))


def _closed(market: str, now: datetime) -> MarketStatus:
    return MarketStatus(market, "closed", now.astimezone(BEIJING), None, None)


def _is_trading_day(day: date, holidays: set[str], extra_open_dates: set[str]) -> bool:
    text = day.isoformat()
    if text in holidays:
        return False
    return day.weekday() < 5 or text in extra_open_dates


def market_status(
    market: str,
    now: datetime | None = None,
    holidays: Iterable[str] = (),
    extra_open_dates: Iterable[str] = (),
    early_closes: Mapping[str, str] | None = None,
    calendar: TradingCalendar | None = None,
) -> MarketStatus:
    if market not in {"a_share", "us_equities"}:
        raise ValueError("market must be a_share or us_equities")
    current = now or datetime.now(BEIJING)
    local_zone = BEIJING if market == "a_share" else NEW_YORK
    local = current.astimezone(local_zone)
    local_day = local.date().isoformat()
    extra_open = set(extra_open_dates)
    holiday_set = set(holidays)
    is_trading_day = (
        calendar.is_trading_day(local.date())
        if calendar is not None
        else _is_trading_day(local.date(), holiday_set, extra_open)
    )
    if market == "a_share" and not is_trading_day:
        return _closed(market, current)
    early_close_text = (
        calendar.early_close(local.date())
        if calendar is not None
        else (early_closes or {}).get(local_day)
    )

    if market == "a_share":
        morning_open = datetime.combine(local.date(), time(9, 30), BEIJING)
        morning_close = datetime.combine(local.date(), time(11, 30), BEIJING)
        afternoon_open = datetime.combine(local.date(), time(13, 0), BEIJING)
        close_time = time.fromisoformat(early_close_text) if early_close_text else time(15, 0)
        close = datetime.combine(local.date(), close_time, BEIJING)
        if morning_open <= local < morning_close or afternoon_open <= local < close:
            state = "open"
        elif morning_close <= local < afternoon_open:
            state = "break"
        elif local < morning_open:
            state = "pre_market"
        else:
            state = "post_market"
        return MarketStatus(market, state, current.astimezone(BEIJING), morning_open, close)

    overnight_after_open = datetime.combine(local.date(), time(20, 0), NEW_YORK)
    next_trading_day = local.date() + timedelta(days=1)
    next_is_trading_day = (
        calendar.is_trading_day(next_trading_day)
        if calendar is not None
        else _is_trading_day(next_trading_day, holiday_set, extra_open)
    )
    if local >= overnight_after_open and next_is_trading_day:
        overnight_close = datetime.combine(next_trading_day, time(3, 50), NEW_YORK)
        return MarketStatus(
            market,
            "overnight",
            current.astimezone(BEIJING),
            overnight_after_open.astimezone(BEIJING),
            overnight_close.astimezone(BEIJING),
        )

    if not is_trading_day:
        return _closed(market, current)

    overnight_open = datetime.combine(local.date() - timedelta(days=1), time(20, 0), NEW_YORK)
    overnight_close = datetime.combine(local.date(), time(3, 50), NEW_YORK)
    pre_market_open = datetime.combine(local.date(), time(4, 0), NEW_YORK)
    session_open = datetime.combine(local.date(), time(9, 30), NEW_YORK)
    close_time = time.fromisoformat(early_close_text) if early_close_text else time(16, 0)
    session_close = datetime.combine(local.date(), close_time, NEW_YORK)
    after_hours_close_time = time(17, 0) if early_close_text else time(20, 0)
    after_hours_close = datetime.combine(local.date(), after_hours_close_time, NEW_YORK)

    if local < overnight_close:
        state = "overnight"
        window_open = overnight_open
        window_close = overnight_close
    elif local < pre_market_open:
        state = "overnight_break"
        window_open = overnight_close
        window_close = pre_market_open
    elif local < session_open:
        state = "pre_market"
        window_open = pre_market_open
        window_close = session_open
    elif local < session_close:
        state = "open"
        window_open = session_open
        window_close = session_close
    elif local < after_hours_close:
        state = "post_market"
        window_open = session_close
        window_close = after_hours_close
    else:
        return _closed(market, current)
    return MarketStatus(
        market,
        state,
        current.astimezone(BEIJING),
        window_open.astimezone(BEIJING),
        window_close.astimezone(BEIJING),
    )
