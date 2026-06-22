"""Feishu (Lark) group-bot webhook conversation port.

Sends structured messages to a Feishu group via the incoming webhook API.
The webhook URL is configured in ``config/settings.json`` under
``feishu.webhook_url`` or via the ``FEISHU_WEBHOOK_URL`` environment variable.
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .intraday import ConversationPort, JsonlConversationPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feishu webhook port
# ---------------------------------------------------------------------------


class FeishuWebhookError(RuntimeError):
    """Raised when the Feishu webhook call fails."""


class FeishuConversationPort:
    """Deliver messages to a Feishu group via a custom-bot webhook.

    The Feishu incoming webhook accepts JSON payloads::

        POST https://open.feishu.cn/open-apis/bot/v2/hook/{hook_id}
        Content-Type: application/json

        {"msg_type": "text", "content": {"text": "..."}}
        {"msg_type": "post", "content": {"post": {...}}}
        {"msg_type": "interactive", "card": {...}}

    This port formats inbound messages into readable Feishu card / text
    messages and falls back to raw JSON for unknown message types.
    """

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    # -- public interface ----------------------------------------------------

    def deliver(self, message: Mapping[str, Any]) -> None:
        """Send *message* to the Feishu group webhook."""
        payload = self._build_payload(message)
        self._post(payload)

    # -- payload formatting --------------------------------------------------

    def _build_payload(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Convert an internal message dict to a Feishu webhook payload."""
        msg_type = message.get("type", "")

        if msg_type == "intraday_agent_discussion":
            return self._format_discussion(message)
        if msg_type == "pre_market_brief":
            return self._format_pre_market_brief(message)
        if msg_type == "post_market_review":
            return self._format_review(message)
        # Suggestion message (has "action" key) or unknown — render as rich
        # text so it looks readable in the group.
        if "action" in message:
            return self._format_suggestion(message)
        return self._format_text(message)

    def _format_text(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Generic fallback: send message as plain text."""
        lines: list[str] = []
        for key, value in message.items():
            if key == "type":
                continue
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
        text = "\n".join(lines) if lines else json.dumps(dict(message), ensure_ascii=False)
        return {"msg_type": "text", "content": {"text": text}}

    def _format_suggestion(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Format an intraday suggestion as a Feishu post (rich text)."""
        market_label = {"a_share": "A股", "us_equities": "美股"}.get(
            message.get("market", ""), message.get("market", "")
        )
        symbol = message.get("symbol", "?")
        action = message.get("action", "?")
        confidence = message.get("confidence", "?")
        rationale = message.get("rationale", "")
        invalidation = message.get("invalidation", "")
        created_at = message.get("created_at", "")

        text_lines = [
            "📊 盘中建议通知",
            "",
            f"市场: {market_label} | 标的: {symbol}",
            f"操作: {action} | 置信度: {confidence}",
            "",
            f"理由: {rationale}",
        ]
        if invalidation:
            text_lines.append(f"失效条件: {invalidation}")
        if created_at:
            text_lines.append(f"时间: {created_at}")

        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"📈 {market_label} {symbol} — {action} ({confidence})",
                        "content": [[{"tag": "text", "text": line}] for line in text_lines if line],
                    }
                }
            },
        }

    def _format_discussion(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Format agent discussion turns as a readable summary."""
        market_label = {"a_share": "A股", "us_equities": "美股"}.get(
            message.get("market", ""), message.get("market", "")
        )
        symbol = message.get("symbol", "?")
        turns: Sequence[Mapping[str, Any]] = message.get("turns", [])

        lines: list[str] = [f"🤖 多 Agent 讨论 — {market_label} {symbol}", ""]
        for turn in turns:
            role = turn.get("role", "?")
            content = turn.get("content", "")
            round_num = turn.get("round", turn.get("round_number", ""))
            prefix = f"[Round {round_num}] " if round_num else ""
            lines.append(f"{prefix}{role}: {content[:500]}")
            lines.append("")

        title = f"🤖 {market_label} {symbol} Agent 讨论 ({len(turns)} 轮)"
        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [[{"tag": "text", "text": line}] for line in lines if line],
                    }
                }
            },
        }

    def _format_pre_market_brief(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Format a generated pre-market brief notification."""
        day = message.get("date", "")
        generated_at = message.get("generated_at", "")
        preview = str(message.get("preview", "")).strip()
        preview_blocks = _preview_blocks_for_feishu(message.get("preview_blocks", []))
        brief_url = _safe_http_url(message.get("brief_url", ""))

        content: list[list[dict[str, str]]] = [[{"tag": "text", "text": f"盘前简报已生成 — {day}"}]]
        if brief_url:
            content.append([{"tag": "a", "text": "查看 HTML 简报", "href": brief_url}])
        else:
            content.append([{"tag": "text", "text": "未配置 Web 简报地址，飞书仅展示摘要预览。"}])
        if preview_blocks:
            content.append([{"tag": "text", "text": "摘要预览:"}])
            content.extend(preview_blocks)
        elif preview:
            content.append([{"tag": "text", "text": "摘要预览:"}])
            content.extend([[{"tag": "text", "text": line}] for line in preview[:2000].splitlines() if line.strip()])
        if generated_at:
            content.append([{"tag": "text", "text": f"时间: {generated_at}"}])

        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"盘前简报 {day}",
                        "content": content,
                    }
                }
            },
        }

    def _format_review(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Format a post-market review summary."""
        market_label = {"a_share": "A股", "us_equities": "美股"}.get(
            message.get("market", ""), message.get("market", "")
        )
        day = message.get("date", "")
        summary = message.get("summary", "")

        lines: list[str] = [f"📋 盘后复盘 — {market_label} {day}", ""]
        if summary:
            lines.append(str(summary)[:2000])
        else:
            lines.append(json.dumps(dict(message), ensure_ascii=False, indent=2)[:2000])

        title = f"📋 {market_label} 盘后复盘 {day}"
        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [[{"tag": "text", "text": line}] for line in lines if line],
                    }
                }
            },
        }

    # -- HTTP ---------------------------------------------------------------

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        """Build an SSL context with CA certs from *certifi* (if available)
        falling back to the system default."""
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    def _post(self, payload: dict[str, Any]) -> None:
        """POST *payload* to the Feishu webhook."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            ctx = self._ssl_context()
            with urllib.request.urlopen(request, timeout=self.timeout, context=ctx) as response:
                resp_body = response.read().decode("utf-8", errors="replace")
                resp_json = json.loads(resp_body)
                code = resp_json.get("code", -1)
                if code != 0:
                    msg = resp_json.get("msg", resp_body[:500])
                    raise FeishuWebhookError(f"Feishu webhook error code={code}: {msg}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise FeishuWebhookError(f"HTTP {exc.code} from Feishu webhook: {detail}") from exc
        except urllib.error.URLError as exc:
            raise FeishuWebhookError(f"Could not reach Feishu webhook: {exc.reason}") from exc
        logger.info("Feishu webhook delivered: msg_type=%s", payload.get("msg_type"))


# ---------------------------------------------------------------------------
# Multi-port: JSONL + Feishu (delivers to both)
# ---------------------------------------------------------------------------


class MultiConversationPort:
    """Deliver messages to multiple ports (e.g. JSONL file + Feishu webhook).

    Errors from individual ports are logged but do not block the remaining
    ports so a transient webhook failure never drops the local JSONL log.
    """

    def __init__(self, ports: Sequence[ConversationPort]) -> None:
        self._ports = list(ports)

    def deliver(self, message: Mapping[str, Any]) -> None:
        for port in self._ports:
            try:
                port.deliver(message)
            except Exception:
                logger.exception("ConversationPort %s failed for message type=%s",
                                 type(port).__name__, message.get("type"))


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def build_outbox(
    root: Path,
    settings: Mapping[str, Any],
) -> ConversationPort:
    """Build the appropriate outbox based on configuration.

    If ``feishu.webhook_url`` is set (in settings or env), returns a
    :class:`MultiConversationPort` that writes to both the JSONL file and the
    Feishu webhook.  Otherwise returns a plain :class:`JsonlConversationPort`.
    """
    state = settings.get("state", {})
    jsonl_path = root / state.get("conversation_outbox", "state/conversation-outbox.jsonl")
    jsonl_port = JsonlConversationPort(jsonl_path)

    feishu_url = _resolve_feishu_url(settings)
    if not feishu_url:
        return jsonl_port

    feishu_port = FeishuConversationPort(
        webhook_url=feishu_url,
        timeout=int(settings.get("feishu", {}).get("timeout_seconds", 10)),
    )
    logger.info("Feishu webhook enabled: %s...%s", feishu_url[:40], feishu_url[-8:])
    return MultiConversationPort([jsonl_port, feishu_port])


def _resolve_feishu_url(settings: Mapping[str, Any]) -> str:
    """Return the Feishu webhook URL from env or settings, or empty string."""
    import os

    env_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url
    settings_url = str(settings.get("feishu", {}).get("webhook_url", "")).strip()
    return settings_url


def _preview_blocks_for_feishu(value: Any) -> list[list[dict[str, str]]]:
    if not isinstance(value, list):
        return []

    blocks: list[list[dict[str, str]]] = []
    char_count = 0
    for raw_block in value:
        if not isinstance(raw_block, list):
            continue
        block: list[dict[str, str]] = []
        for raw_token in raw_block:
            if not isinstance(raw_token, Mapping):
                continue
            text = str(raw_token.get("text", "")).strip()
            if not text:
                continue
            tag = raw_token.get("tag")
            if tag == "a":
                href = str(raw_token.get("href", "")).strip()
                if _safe_http_url(href):
                    block.append({"tag": "a", "text": text[:300], "href": href})
                else:
                    block.append({"tag": "text", "text": text[:300]})
            else:
                block.append({"tag": "text", "text": text[:500]})
            char_count += len(text)
            if char_count >= 2000:
                break
        if block:
            blocks.append(block)
        if char_count >= 2000 or len(blocks) >= 24:
            break
    return blocks


def _safe_http_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    return ""
