from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from marketanalyzeragents.market_calendar import market_status


class MarketCalendarTests(unittest.TestCase):
    def test_a_share_midday_break_is_not_open(self) -> None:
        now = datetime(2026, 6, 9, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(market_status("a_share", now).state, "break")

    def test_us_session_is_converted_using_daylight_saving(self) -> None:
        now = datetime(2026, 6, 9, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("us_equities", now)
        self.assertEqual(status.state, "open")
        self.assertEqual(status.session_open_beijing.hour, 21)
        self.assertEqual(status.session_open_beijing.minute, 30)

    def test_us_winter_session_has_different_beijing_open(self) -> None:
        now = datetime(2026, 1, 9, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("us_equities", now)
        self.assertEqual(status.session_open_beijing.hour, 22)
        self.assertEqual(status.session_open_beijing.minute, 30)

    def test_configured_extra_open_day_handles_makeup_trading_day(self) -> None:
        now = datetime(2026, 6, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("a_share", now, extra_open_dates=["2026-06-13"])
        self.assertEqual(status.state, "open")

    def test_configured_early_close_ends_us_session_before_regular_close(self) -> None:
        now = datetime(2026, 7, 3, 2, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status(
            "us_equities",
            now,
            early_closes={"2026-07-02": "13:00"},
        )
        self.assertEqual(status.state, "post_market")


if __name__ == "__main__":
    unittest.main()
