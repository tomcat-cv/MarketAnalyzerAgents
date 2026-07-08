from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from marketanalyzeragents.market_calendar import calendar_from_settings, market_status


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

    def test_us_sunday_night_is_overnight_during_daylight_saving(self) -> None:
        now = datetime(2026, 6, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("us_equities", now)
        self.assertEqual(status.state, "overnight")
        self.assertEqual(status.session_open_beijing.hour, 8)
        self.assertEqual(status.session_close_beijing.hour, 15)
        self.assertEqual(status.session_close_beijing.minute, 50)

    def test_us_sunday_night_is_overnight_during_standard_time(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("us_equities", now)
        self.assertEqual(status.state, "overnight")
        self.assertEqual(status.session_open_beijing.hour, 9)
        self.assertEqual(status.session_close_beijing.hour, 16)
        self.assertEqual(status.session_close_beijing.minute, 50)

    def test_us_overnight_break_is_not_reported_as_closed(self) -> None:
        now = datetime(2026, 6, 9, 15, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
        status = market_status("us_equities", now)
        self.assertEqual(status.state, "overnight_break")

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

    def test_file_calendar_overrides_weekend_and_early_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar_path = root / "us-calendar.json"
            calendar_path.write_text(
                json.dumps(
                    {
                        "source": "unit-test-calendar",
                        "trading_days": ["2026-07-04"],
                        "early_closes": {"2026-07-04": "13:00"},
                    }
                ),
                encoding="utf-8",
            )
            calendar = calendar_from_settings(
                {
                    "calendar": {
                        "provider": "file",
                        "path": "us-calendar.json",
                        "strict": True,
                    }
                },
                root=root,
            )

        open_status = market_status(
            "us_equities",
            datetime(2026, 7, 4, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            calendar=calendar,
        )
        closed_status = market_status(
            "us_equities",
            datetime(2026, 7, 5, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            calendar=calendar,
        )

        self.assertEqual(open_status.state, "open")
        self.assertEqual(closed_status.state, "post_market")

    def test_repo_calendars_include_2026_market_holidays(self) -> None:
        root = Path(__file__).resolve().parents[1]
        a_share_calendar = calendar_from_settings(
            {
                "calendar": {
                    "provider": "file",
                    "path": "config/calendars/a_share_2026.json",
                    "strict": True,
                }
            },
            root=root,
        )
        us_calendar = calendar_from_settings(
            {
                "calendar": {
                    "provider": "file",
                    "path": "config/calendars/us_equities_2026.json",
                    "strict": True,
                }
            },
            root=root,
        )

        self.assertEqual(
            market_status("a_share", datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), calendar=a_share_calendar).state,
            "closed",
        )
        self.assertEqual(
            market_status("us_equities", datetime(2026, 7, 3, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai")), calendar=us_calendar).state,
            "closed",
        )

    def test_repo_us_holiday_calendar_keeps_regular_weekdays_trading(self) -> None:
        root = Path(__file__).resolve().parents[1]
        us_calendar = calendar_from_settings(
            {
                "calendar": {
                    "provider": "file",
                    "path": "config/calendars/us_equities_2026.json",
                    "strict": True,
                }
            },
            root=root,
        )

        status = market_status("us_equities", datetime(2026, 7, 8, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai")), calendar=us_calendar)

        self.assertEqual(status.state, "pre_market")


if __name__ == "__main__":
    unittest.main()
