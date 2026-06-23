from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable, Mapping
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


def _closed(market: str, now: datetime) -> MarketStatus:
    return MarketStatus(market, "closed", now.astimezone(BEIJING), None, None)


def market_status(
    market: str,
    now: datetime | None = None,
    holidays: Iterable[str] = (),
    extra_open_dates: Iterable[str] = (),
    early_closes: Mapping[str, str] | None = None,
) -> MarketStatus:
    if market not in {"a_share", "us_equities"}:
        raise ValueError("market must be a_share or us_equities")
    current = now or datetime.now(BEIJING)
    local_zone = BEIJING if market == "a_share" else NEW_YORK
    local = current.astimezone(local_zone)
    local_day = local.date().isoformat()
    extra_open = set(extra_open_dates)
    if (local.weekday() >= 5 and local_day not in extra_open) or local_day in set(holidays):
        return _closed(market, current)
    early_close_text = (early_closes or {}).get(local_day)

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

    session_open = datetime.combine(local.date(), time(9, 30), NEW_YORK)
    close_time = time.fromisoformat(early_close_text) if early_close_text else time(16, 0)
    session_close = datetime.combine(local.date(), close_time, NEW_YORK)
    if session_open <= local < session_close:
        state = "open"
    elif local < session_open:
        state = "pre_market"
    else:
        state = "post_market"
    return MarketStatus(
        market,
        state,
        current.astimezone(BEIJING),
        session_open.astimezone(BEIJING),
        session_close.astimezone(BEIJING),
    )
