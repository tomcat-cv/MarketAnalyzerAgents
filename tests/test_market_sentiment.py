import unittest
from unittest.mock import patch

from marketanalyzeragents.intraday import PriceBar
from marketanalyzeragents.market_sentiment import (
    _parse_cboe_put_call,
    _parse_fred_latest,
    collect_market_sentiment,
)


class FakeClient:
    def get_text(self, url):
        return ""


def bar(close, index=0):
    return PriceBar(
        market="us_equities",
        symbol="TEST",
        interval="1d",
        observed_at=f"2026-07-{index + 1:02d}T00:00:00+00:00",
        open=close,
        high=close,
        low=close,
        close=close,
    )


class MarketSentimentTests(unittest.TestCase):
    def test_parses_cboe_equity_put_call_csv(self) -> None:
        text = "intro,,,,\nDATE,CALL,PUT,TOTAL,P/C Ratio\n7/6/2026,10,5,15,0.50\n7/7/2026,10,8,18,0.80\n"

        ratio, observed_at = _parse_cboe_put_call(text)

        self.assertEqual(ratio, 0.80)
        self.assertEqual(observed_at, "7/7/2026")

    def test_parses_fred_latest_csv(self) -> None:
        value, observed_at = _parse_fred_latest(
            "observation_date,BAMLH0A0HYM2\n2026-07-06,2.72\n2026-07-07,.\n",
            "BAMLH0A0HYM2",
        )

        self.assertEqual(value, 2.72)
        self.assertEqual(observed_at, "2026-07-06")

    def test_collect_market_sentiment_reweights_available_components(self) -> None:
        def fake_history(client, symbol, range_value="1y"):
            if symbol == "^VIX":
                return [bar(15, 1)]
            if symbol == "^VVIX":
                return [bar(85, 1)]
            if symbol == "^GSPC":
                return [bar(100 + index, index) for index in range(220)]
            if symbol == "SPY":
                return [bar(100, 1), bar(110, 2)]
            if symbol == "TLT":
                return [bar(100, 1), bar(95, 2)]
            raise ValueError(symbol)

        class TextClient:
            def get_text(self, url):
                if "cboe" in url:
                    return "intro,,,,\nDATE,CALL,PUT,TOTAL,P/C Ratio\n7/7/2026,10,5,15,0.50\n"
                return "observation_date,BAMLH0A0HYM2\n2026-07-06,2.72\n"

        with patch("marketanalyzeragents.market_sentiment._fetch_daily_history", side_effect=fake_history):
            sentiment = collect_market_sentiment(TextClient())

        self.assertEqual(sentiment["status"], "partial")
        self.assertTrue(0 <= sentiment["score"] <= 100)
        self.assertGreater(sentiment["available_weight"], 0.7)
        self.assertEqual(len(sentiment["components"]), 9)


if __name__ == "__main__":
    unittest.main()
