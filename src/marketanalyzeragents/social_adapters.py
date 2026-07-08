from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .collectors_core import strip_html


SUPPORTED_PLATFORMS = ("x", "xiaohongshu")


@dataclass
class SocialPost:
    platform: str
    author: str
    published_at: str
    url: str
    text: str
    keywords: list[str] = field(default_factory=list)
    sentiment: str = "neutral"


class SocialAdapter(Protocol):
    def collect(self, platform: str, config: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]: ...


def classify_sentiment(text: str) -> str:
    value = text.casefold()
    positive = ("看多", "利好", "上涨", "突破", "增长", "超预期", "bull", "buy", "positive")
    negative = ("看空", "利空", "下跌", "破位", "衰退", "低预期", "bear", "sell", "negative")
    pos = sum(1 for word in positive if word in value)
    neg = sum(1 for word in negative if word in value)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        pieces = value.replace("，", ",").replace("\n", ",").split(",")
    elif isinstance(value, list):
        pieces = [str(item) for item in value]
    else:
        return []
    return [item.strip() for item in pieces if item.strip()]


class ManualSocialAdapter:
    def collect(self, platform: str, config: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]:
        posts: list[SocialPost] = []
        keywords = string_list(config.get("keywords", []))
        for raw_post in config.get("manual_posts", []):
            if not isinstance(raw_post, Mapping):
                continue
            text = strip_html(str(raw_post.get("text", "")).strip())
            if not text:
                continue
            posts.append(
                SocialPost(
                    platform=platform,
                    author=str(raw_post.get("author", "")).strip(),
                    published_at=str(raw_post.get("published_at", "")).strip(),
                    url=str(raw_post.get("url", "")).strip(),
                    text=text,
                    keywords=keywords,
                    sentiment=str(raw_post.get("sentiment") or classify_sentiment(text)),
                )
            )
        return posts, []


class DisabledSocialAdapter:
    def collect(self, platform: str, config: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]:
        if config.get("accounts") or config.get("keywords"):
            return [], [f"{platform} 已配置账号/关键词，但未启用可用采集适配器；未伪造平台数据。"]
        return [], []


def build_social_adapter(name: str) -> SocialAdapter:
    if name == "manual":
        return ManualSocialAdapter()
    if name in {"", "disabled"}:
        return DisabledSocialAdapter()
    raise ValueError(f"unsupported social adapter: {name}")


def collect_social_posts(sources: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]:
    social = sources.get("social_sources", {})
    if not isinstance(social, Mapping):
        return [], []
    posts: list[SocialPost] = []
    warnings: list[str] = []
    for platform in SUPPORTED_PLATFORMS:
        config = social.get(platform, {})
        if not isinstance(config, Mapping) or not config.get("enabled", True):
            continue
        adapter_name = str(config.get("adapter", "manual" if config.get("manual_posts") else "disabled")).strip()
        adapter = build_social_adapter(adapter_name)
        collected, adapter_warnings = adapter.collect(platform, config)
        posts.extend(collected)
        warnings.extend(adapter_warnings)
    return posts, warnings
