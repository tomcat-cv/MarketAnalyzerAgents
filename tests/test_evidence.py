import unittest

from marketanalyzeragents.evidence import EvidenceItem, configured_portfolio_holdings, dedupe_evidence


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


if __name__ == "__main__":
    unittest.main()
