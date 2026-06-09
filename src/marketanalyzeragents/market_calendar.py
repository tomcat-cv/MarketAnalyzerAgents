from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable
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
) -> MarketStatus:
    if market not in {"a_share", "us_equities"}:
        raise ValueError("market must be a_share or us_equities")
    current = now or datetime.now(BEIJING)
    local_zone = BEIJING if market == "a_share" else NEW_YORK
    local = current.astimezone(local_zone)
    if local.weekday() >= 5 or local.date().isoformat() in set(holidays):
        return _closed(market, current)

    if market == "a_share":
        morning_open = datetime.combine(local.date(), time(9, 30), BEIJING)
        morning_close = datetime.combine(local.date(), time(11, 30), BEIJING)
        afternoon_open = datetime.combine(local.date(), time(13, 0), BEIJING)
        close = datetime.combine(local.date(), time(15, 0), BEIJING)
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
    session_close = datetime.combine(local.date(), time(16, 0), NEW_YORK)
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
