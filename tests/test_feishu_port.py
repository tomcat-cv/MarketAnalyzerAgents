"""Tests for the Feishu webhook conversation port."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from marketanalyzeragents.feishu_port import (
    FeishuConversationPort,
    FeishuWebhookError,
    MultiConversationPort,
    build_outbox,
    _resolve_feishu_url,
)
from marketanalyzeragents.intraday import JsonlConversationPort


class FeishuPayloadFormattingTests(unittest.TestCase):
    """Test that messages are correctly formatted into Feishu payloads."""

    def setUp(self) -> None:
        self.port = FeishuConversationPort("https://example.com/hook/test")

    def test_format_suggestion(self) -> None:
        message = {
            "type": "suggestion",
            "market": "us_equities",
            "symbol": "NVDA",
            "action": "观察",
            "confidence": "中",
            "rationale": "价格变化 +2.50%",
            "invalidation": "行情数据过期时重新评估",
            "created_at": "2026-06-09T10:00:00+08:00",
        }
        payload = self.port._build_payload(message)
        self.assertEqual(payload["msg_type"], "post")
        post = payload["content"]["post"]["zh_cn"]
        self.assertIn("NVDA", post["title"])
        self.assertIn("美股", post["title"])

    def test_format_discussion(self) -> None:
        message = {
            "type": "intraday_agent_discussion",
            "market": "a_share",
            "symbol": "600519",
            "turns": [
                {"role": "行情分析师", "round_number": 1, "content": "价格稳定"},
                {"role": "新闻分析师", "round_number": 1, "content": "无重大新闻"},
            ],
        }
        payload = self.port._build_payload(message)
        self.assertEqual(payload["msg_type"], "post")
        post = payload["content"]["post"]["zh_cn"]
        self.assertIn("A股", post["title"])
        self.assertIn("Agent 讨论", post["title"])

    def test_format_review(self) -> None:
        message = {
            "type": "post_market_review",
            "market": "us_equities",
            "date": "2026-06-09",
            "summary": "市场整体上涨",
        }
        payload = self.port._build_payload(message)
        self.assertEqual(payload["msg_type"], "post")
        post = payload["content"]["post"]["zh_cn"]
        self.assertIn("盘后复盘", post["title"])

    def test_format_unknown_falls_back_to_text(self) -> None:
        message = {"type": "something_else", "key": "value"}
        payload = self.port._build_payload(message)
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("key", payload["content"]["text"])


class FeishuHttpPostTests(unittest.TestCase):
    """Test the HTTP POST behaviour."""

    @patch("urllib.request.urlopen")
    def test_post_success(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"code": 0, "msg": "ok"}).encode()
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        port = FeishuConversationPort("https://open.feishu.cn/open-apis/bot/v2/hook/test123")
        port.deliver({"type": "test", "key": "value"})
        mock_urlopen.assert_called_once()

        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.method, "POST")
        self.assertIn("application/json", request.get_header("Content-type"))

    @patch("urllib.request.urlopen")
    def test_post_api_error(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"code": 19001, "msg": "invalid webhook"}).encode()
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        port = FeishuConversationPort("https://open.feishu.cn/open-apis/bot/v2/hook/test123")
        with self.assertRaises(FeishuWebhookError) as ctx:
            port.deliver({"type": "test"})
        self.assertIn("19001", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_post_http_error(self, mock_urlopen: MagicMock) -> None:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://open.feishu.cn/test",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        # HTTPError reads the body in our code; make read() available
        mock_urlopen.side_effect.read = lambda: b"forbidden"

        port = FeishuConversationPort("https://open.feishu.cn/open-apis/bot/v2/hook/test123")
        with self.assertRaises(FeishuWebhookError) as ctx:
            port.deliver({"type": "test"})
        self.assertIn("HTTP 403", str(ctx.exception))


class MultiPortTests(unittest.TestCase):
    """Test MultiConversationPort error isolation."""

    def test_one_port_failure_does_not_block_others(self) -> None:
        jsonl_calls: list[dict] = []
        failing = MagicMock()
        failing.deliver.side_effect = RuntimeError("webhook down")
        jsonl_port = MagicMock()
        jsonl_port.deliver.side_effect = lambda msg: jsonl_calls.append(msg)

        multi = MultiConversationPort([failing, jsonl_port])
        # Should not raise despite first port failing
        multi.deliver({"type": "test", "action": "hold"})
        self.assertEqual(len(jsonl_calls), 1)

    def test_all_ports_called(self) -> None:
        p1 = MagicMock()
        p2 = MagicMock()
        multi = MultiConversationPort([p1, p2])
        multi.deliver({"type": "test"})
        p1.deliver.assert_called_once()
        p2.deliver.assert_called_once()


class BuildOutboxTests(unittest.TestCase):
    """Test the build_outbox factory."""

    def test_no_feishu_url_returns_jsonl_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {"state": {"conversation_outbox": "state/outbox.jsonl"}, "feishu": {"webhook_url": ""}}
            outbox = build_outbox(root, settings)
            self.assertIsInstance(outbox, JsonlConversationPort)

    def test_feishu_url_returns_multi_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {
                "state": {"conversation_outbox": "state/outbox.jsonl"},
                "feishu": {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc123", "timeout_seconds": 5},
            }
            outbox = build_outbox(root, settings)
            self.assertIsInstance(outbox, MultiConversationPort)

    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/envvar"}, clear=False)
    def test_env_var_overrides_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = {"state": {"conversation_outbox": "state/outbox.jsonl"}, "feishu": {"webhook_url": ""}}
            outbox = build_outbox(root, settings)
            self.assertIsInstance(outbox, MultiConversationPort)


class ResolveFeishuUrlTests(unittest.TestCase):
    def test_settings_url(self) -> None:
        url = _resolve_feishu_url({"feishu": {"webhook_url": "https://example.com/hook/abc"}})
        self.assertEqual(url, "https://example.com/hook/abc")

    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "  "}, clear=False)
    def test_empty_env_falls_back_to_settings(self) -> None:
        url = _resolve_feishu_url({"feishu": {"webhook_url": "https://example.com/hook/abc"}})
        self.assertEqual(url, "https://example.com/hook/abc")

    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "https://env.com/hook/xyz"}, clear=False)
    def test_env_overrides_settings(self) -> None:
        url = _resolve_feishu_url({"feishu": {"webhook_url": "https://settings.com/hook/abc"}})
        self.assertEqual(url, "https://env.com/hook/xyz")

    def test_no_url_returns_empty(self) -> None:
        url = _resolve_feishu_url({"feishu": {"webhook_url": ""}})
        self.assertEqual(url, "")


if __name__ == "__main__":
    unittest.main()
