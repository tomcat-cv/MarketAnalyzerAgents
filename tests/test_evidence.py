import unittest
from unittest.mock import patch

from marketanalyzeragents.evidence import EvidenceItem, configured_portfolio_holdings, dedupe_evidence
from marketanalyzeragents.social_adapters import collect_social_posts


def evidence(url="https://official.example/release") -> EvidenceItem:
    return EvidenceItem(
        id="",
        title="Signal",
        published_at="2026-06-04T08:00:00+00:00",
        source_name="Official",
        source_type="primary",
        url=url,
        content="Verified fact",
    )


class EvidenceContractTests(unittest.TestCase):
    def test_portfolios_remain_separated_and_symbols_are_normalized(self) -> None:
        holdings = configured_portfolio_holdings(
            {
                "portfolios": {
                    "us_equities": {"holdings": [{"ticker": "nvda"}]},
                    "a_share": {"holdings": [{"ticker": "688001"}]},
                }
            }
        )
        self.assertEqual(
            [(item["market"], item["ticker"]) for item in holdings],
            [("a_share", "688001"), ("us_equities", "NVDA")],
        )

    def test_deduplication_assigns_stable_ids(self) -> None:
        items = dedupe_evidence(
            [
                evidence("https://example.com/a"),
                evidence("https://example.com/a#fragment"),
                EvidenceItem(
                    **{
                        **evidence("https://example.com/b").__dict__,
                        "title": "Another signal",
                    }
                ),
            ],
            10,
        )
        self.assertEqual([item.id for item in items], ["EVID-001", "EVID-002"])

    def test_disabled_social_adapter_warns_without_fabricating_posts(self) -> None:
        posts, warnings = collect_social_posts(
            {
                "social_sources": {
                    "x": {
                        "enabled": True,
                        "adapter": "disabled",
                        "keywords": ["NVDA"],
                        "accounts": ["analyst"],
                    }
                }
            }
        )

        self.assertEqual(posts, [])
        self.assertTrue(warnings)

    def test_x_api_adapter_requires_twitterapi_io_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            posts, warnings = collect_social_posts(
                {
                    "social_sources": {
                        "x": {
                            "enabled": True,
                            "adapter": "x_api",
                            "keywords": ["NVDA"],
                            "accounts": ["analyst"],
                        }
                    }
                }
            )

        self.assertEqual(posts, [])
        self.assertIn("TWITTERAPI_IO_API_KEY", warnings[0])

    def test_x_api_adapter_collects_twitterapi_io_response_shape(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.urls = []
                self.headers = {}

            def request_bytes(self, url, *, headers=None, **_kwargs):
                self.urls.append(url)
                self.headers = headers or {}
                if "advanced_search" in url:
                    return b"""{
                        "tweets": [
                            {
                                "id": "123",
                                "createdAt": "Sun Jul 12 08:00:00 +0000 2026",
                                "url": "https://x.com/analyst/status/123",
                                "text": "NVDA bull breakout",
                                "author": {"id": "u1", "userName": "analyst", "name": "Analyst"}
                            }
                        ],
                        "has_next_page": false,
                        "next_cursor": "",
                        "status": "success"
                    }"""
                return b"""{
                    "data": {
                        "tweets": [
                            {
                                "id": "456",
                                "createdAt": "Sun Jul 12 09:00:00 +0000 2026",
                                "url": "https://x.com/analyst/status/456",
                                "text": "AI capex remains positive",
                                "author": {"id": "u1", "userName": "analyst", "name": "Analyst"}
                            }
                        ]
                    },
                    "has_next_page": false,
                    "next_cursor": "",
                    "status": "success"
                }"""

        client = FakeClient()
        with patch.dict("os.environ", {"TWITTERAPI_IO_API_KEY": "token"}, clear=True):
            posts, warnings = collect_social_posts(
                {
                    "social_sources": {
                        "x": {
                            "enabled": True,
                            "adapter": "x_api",
                            "keywords": ["NVDA", "AI capex"],
                            "accounts": ["analyst"],
                            "max_results": 5,
                        }
                    }
                },
                client=client,
            )

        self.assertEqual(warnings, [])
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].author, "analyst")
        self.assertEqual(posts[0].url, "https://x.com/analyst/status/123")
        self.assertEqual(posts[0].sentiment, "positive")
        self.assertEqual(posts[0].collection_type, "keyword")
        self.assertEqual(posts[1].collection_type, "account")
        self.assertIn("/twitter/tweet/advanced_search?", client.urls[0])
        self.assertIn("NVDA+OR+%22AI+capex%22", client.urls[0])
        self.assertIn("/twitter/user/last_tweets?", client.urls[1])
        self.assertIn("userName=analyst", client.urls[1])
        self.assertEqual(client.headers["X-API-Key"], "token")

    def test_x_api_adapter_limits_keyword_and_account_posts_separately(self) -> None:
        class FakeClient:
            def request_bytes(self, url, *, headers=None, **_kwargs):
                if "advanced_search" in url:
                    return b"""{
                        "tweets": [
                            {"id": "k1", "url": "https://x.com/search/status/k1", "text": "NVDA bull", "author": {"userName": "search"}},
                            {"id": "k2", "url": "https://x.com/search/status/k2", "text": "NVDA positive", "author": {"userName": "search"}}
                        ],
                        "status": "success"
                    }"""
                return b"""{
                    "data": {
                        "tweets": [
                            {"id": "a1", "url": "https://x.com/analyst/status/a1", "text": "account one", "author": {"userName": "analyst"}},
                            {"id": "a2", "url": "https://x.com/analyst/status/a2", "text": "account two", "author": {"userName": "analyst"}}
                        ]
                    },
                    "status": "success"
                }"""

        with patch.dict("os.environ", {"TWITTERAPI_IO_API_KEY": "token"}, clear=True):
            posts, warnings = collect_social_posts(
                {
                    "social_sources": {
                        "x": {
                            "enabled": True,
                            "adapter": "x_api",
                            "keywords": ["NVDA"],
                            "accounts": ["analyst"],
                            "keyword_max_results": 1,
                            "account_max_results_per_account": 2,
                        }
                    }
                },
                client=FakeClient(),
            )

        self.assertEqual(warnings, [])
        self.assertEqual([post.url for post in posts], [
            "https://x.com/search/status/k1",
            "https://x.com/analyst/status/a1",
            "https://x.com/analyst/status/a2",
        ])


if __name__ == "__main__":
    unittest.main()
