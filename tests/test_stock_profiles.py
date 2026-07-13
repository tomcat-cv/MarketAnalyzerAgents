import unittest

from marketanalyzeragents.stock_profiles import lookup_stock_profile


class FakeClient:
    def get_json(self, url):
        localized = "lang=zh-Hant" in url
        return {"quotes": [{
            "quoteType": "EQUITY", "symbol": "NVDA", "exchange": "NMS",
            "longname": "英伟达" if localized else "NVIDIA Corporation",
            "sector": "科技" if localized else "Technology",
            "industry": "半导体" if localized else "Semiconductors",
        }]}


class StockProfileTests(unittest.TestCase):
    def test_us_equity_profile_is_verified_and_enriched(self):
        profile = lookup_stock_profile(FakeClient(), "us_equities", "nvda")

        self.assertTrue(profile["verified"])
        self.assertEqual(profile["company_name_zh"], "英伟达")
        self.assertEqual(profile["company_name_en"], "NVIDIA Corporation")
        self.assertIn("Semiconductors", profile["business_domains"])
        self.assertEqual(profile["official_sources"][0]["name"], "SEC EDGAR")

    def test_unknown_market_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "market"):
            lookup_stock_profile(FakeClient(), "crypto", "BTC")


if __name__ == "__main__":
    unittest.main()
