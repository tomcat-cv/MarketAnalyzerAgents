from datetime import datetime, timedelta, timezone
import urllib.error
import unittest
from unittest.mock import patch

from marketanalyzeragents.collectors import HttpClient, collect_rss_items
from marketanalyzeragents.collectors_core import resolve_research_window
from marketanalyzeragents.analysis_system import _collect_official


class FakeClient:
    def request_bytes(self, url, **kwargs):
        return b"""<?xml version="1.0"?>
<rss><channel><item>
<title>Official release</title>
<link>https://official.example/release</link>
<pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
<description>Verified summary</description>
</item></channel></rss>"""


class NoSummaryFeedClient(FakeClient):
    def request_bytes(self, url, **kwargs):
        return b"""<?xml version="1.0"?>
<rss><channel><item>
<title>Official release title only</title>
<link>https://official.example/title-only</link>
<pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
</item></channel></rss>"""


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 6, 3, tzinfo=timezone.utc)

    def test_http_client_retries_transient_network_failures(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b"ok"

        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise urllib.error.URLError("temporary dns failure")
            return Response()

        client = HttpClient("test-agent", max_retries=1, retry_backoff_seconds=0)
        with patch("marketanalyzeragents.collectors_core.urllib.request.urlopen", side_effect=fake_urlopen):
            body = client.request_bytes("https://example.com/feed")

        self.assertEqual(body, b"ok")
        self.assertEqual(len(calls), 2)

    def test_collects_allowed_rss_items(self) -> None:
        items = collect_rss_items(
            client=FakeClient(),
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
                "tickers": ["NVDA"],
            },
            cutoff=self.cutoff,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://official.example/release")
        self.assertEqual(items[0].evidence_level, "summary")
        self.assertEqual(items[0].matched_tickers, ["NVDA"])

    def test_rss_without_summary_is_title_only(self) -> None:
        items = collect_rss_items(
            client=NoSummaryFeedClient(),
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
            },
            cutoff=self.cutoff,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].evidence_level, "title_only")

    def test_previous_day_window_starts_at_local_midnight(self) -> None:
        china_tz = timezone(timedelta(hours=8))
        start, end, mode = resolve_research_window(
            {"timezone": "Asia/Shanghai", "freshness_window": "previous_day_to_run"},
            now=datetime(2026, 6, 4, 6, 30, tzinfo=china_tz),
        )
        self.assertEqual(mode, "previous_day_to_run")
        self.assertEqual(start, datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 3, 22, 30, tzinfo=timezone.utc))

    def test_rss_excludes_items_after_run_time(self) -> None:
        items = collect_rss_items(
            client=FakeClient(),
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
            },
            cutoff=self.cutoff,
            window_end=datetime(2026, 6, 4, 7, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(items, [])

    def test_official_collection_uses_resolved_window_tuple(self) -> None:
        with patch("marketanalyzeragents.analysis_system.HttpClient", return_value=FakeClient()):
            items, warnings = _collect_official(
                root=None,
                settings={
                    "timezone": "Asia/Shanghai",
                    "freshness_window": "rolling_hours",
                    "lookback_hours": 2000,
                    "collectors": {},
                },
                sources={
                    "official_sources": [
                        {
                            "type": "rss",
                            "enabled": True,
                            "name": "Official Feed",
                            "url": "https://official.example/feed.xml",
                            "allowed_domains": ["official.example"],
                        }
                    ]
                },
            )

        self.assertEqual(warnings, [])
        self.assertEqual(len(items), 1)

if __name__ == "__main__":
    unittest.main()
