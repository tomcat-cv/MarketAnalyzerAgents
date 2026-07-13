from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .collectors_core import CollectionError, HttpClient, strip_html


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
    collection_type: str = "unknown"


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
                    collection_type="manual",
                )
            )
        return posts, []


class DisabledSocialAdapter:
    def collect(self, platform: str, config: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]:
        if config.get("accounts") or config.get("keywords"):
            return [], [f"{platform} 已配置账号/关键词，但未配置可用采集方式；未伪造平台数据。"]
        return [], []


class XApiSocialAdapter:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            os.environ.get("MARKET_ANALYZER_AGENTS_USER_AGENT", "market-analyzer-agents/0.1"),
            timeout=30,
            max_retries=2,
            retry_backoff_seconds=1.0,
        )

    def collect(self, platform: str, config: Mapping[str, Any]) -> tuple[list[SocialPost], list[str]]:
        if platform != "x":
            return [], [f"{platform} 不支持 x_api 采集器。"]
        api_key_env = str(config.get("api_key_env") or "TWITTERAPI_IO_API_KEY").strip()
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            return [], [f"x_api 缺少 {api_key_env}；未采集 X 数据。"]

        queries = _build_twitterapi_queries(config)
        accounts = [item.lstrip("@") for item in string_list(config.get("accounts", [])) if item.lstrip("@")]
        if not queries and not accounts:
            return [], ["x_api 未配置账号或关键词；未采集 X 数据。"]

        keyword_max_results = _x_max_results(config.get("keyword_max_results", config.get("max_results", 20)))
        account_max_results_per_account = _x_max_results(
            config.get("account_max_results_per_account", config.get("max_results", 20))
        )
        request_interval = _non_negative_float(config.get("request_interval_seconds", 0))
        api_base = str(config.get("api_base") or "https://api.twitterapi.io").rstrip("/")
        headers = {"X-API-Key": api_key}
        keyword_posts: list[SocialPost] = []
        account_posts: list[SocialPost] = []
        warnings: list[str] = []
        last_request_at = 0.0
        for query in queries:
            last_request_at = _wait_for_request_interval(last_request_at, request_interval)
            params = {"query": query, "queryType": str(config.get("query_type") or "Latest")}
            url = f"{api_base}/twitter/tweet/advanced_search?{urllib.parse.urlencode(params)}"
            payload, warning = self._request_json(url, headers)
            if warning:
                warnings.append(warning)
                continue
            keyword_posts.extend(_parse_twitterapi_tweets(payload, config, collection_type="keyword"))
        for account in accounts:
            last_request_at = _wait_for_request_interval(last_request_at, request_interval)
            params = {
                "userName": account,
                "includeReplies": str(bool(config.get("include_replies", False))).lower(),
            }
            url = f"{api_base}/twitter/user/last_tweets?{urllib.parse.urlencode(params)}"
            payload, warning = self._request_json(url, headers)
            if warning:
                warnings.append(warning)
                continue
            account_posts.extend(_parse_twitterapi_tweets(payload, config, collection_type="account")[:account_max_results_per_account])
        limited_keyword_posts = _dedupe_social_posts(keyword_posts)[:keyword_max_results]
        limited_account_posts = _dedupe_social_posts(account_posts)
        return _dedupe_social_posts(limited_keyword_posts + limited_account_posts), warnings

    def _request_json(self, url: str, headers: Mapping[str, str]) -> tuple[Mapping[str, Any], str]:
        try:
            payload = json.loads(self.client.request_bytes(url, headers=headers).decode("utf-8"))
        except (CollectionError, json.JSONDecodeError) as exc:
            return {}, f"x_api: {exc}"
        if not isinstance(payload, Mapping):
            return {}, "x_api: 响应不是 JSON object"
        if str(payload.get("status", "success")).lower() == "error":
            return {}, f"x_api: {payload.get('message', 'unknown error')}"
        return payload, ""


def _x_max_results(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 20
    return min(100, max(1, count))


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _wait_for_request_interval(last_request_at: float, request_interval: float) -> float:
    if last_request_at and request_interval:
        elapsed = time.monotonic() - last_request_at
        if elapsed < request_interval:
            time.sleep(request_interval - elapsed)
    return time.monotonic()


def _twitterapi_query_term(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if " " in value or "\t" in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _build_twitterapi_queries(config: Mapping[str, Any]) -> list[str]:
    explicit = str(config.get("query") or "").strip()
    if explicit:
        return [_apply_twitterapi_query_filters(explicit, config)]
    keyword_terms = [_twitterapi_query_term(item) for item in string_list(config.get("keywords", []))]
    terms = [item for item in keyword_terms if item]
    if not terms:
        return []
    query = " OR ".join(terms)
    if len(terms) > 1:
        query = f"({query})"
    return [_apply_twitterapi_query_filters(query, config)]


def _apply_twitterapi_query_filters(query: str, config: Mapping[str, Any]) -> str:
    if config.get("exclude_retweets", True):
        query = f"{query} -filter:nativeretweets"
    if config.get("exclude_replies", False):
        query = f"{query} -filter:replies"
    language = str(config.get("language") or "").strip()
    if language:
        query = f"{query} lang:{language}"
    return query[:512]


def _parse_twitterapi_tweets(
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    collection_type: str = "unknown",
) -> list[SocialPost]:
    posts: list[SocialPost] = []
    keywords = string_list(config.get("keywords", []))
    raw_tweets = payload.get("tweets")
    data = payload.get("data")
    if not isinstance(raw_tweets, list) and isinstance(data, Mapping):
        raw_tweets = data.get("tweets", [])
    if not isinstance(raw_tweets, list):
        return posts
    for item in raw_tweets:
        if not isinstance(item, Mapping):
            continue
        if config.get("exclude_retweets", True) and item.get("retweeted_tweet"):
            continue
        if config.get("exclude_replies", False) and item.get("isReply"):
            continue
        text = strip_html(str(item.get("text", "")).strip())
        tweet_id = str(item.get("id", "")).strip()
        if not text or not tweet_id:
            continue
        author = item.get("author", {})
        username = ""
        if isinstance(author, Mapping):
            username = str(author.get("userName") or author.get("username") or author.get("id") or "").strip()
        posts.append(
            SocialPost(
                platform="x",
                author=username,
                published_at=str(item.get("createdAt") or item.get("created_at") or "").strip(),
                url=str(item.get("url") or "").strip()
                or (f"https://x.com/{username}/status/{tweet_id}" if username else f"https://x.com/i/web/status/{tweet_id}"),
                text=text,
                keywords=keywords,
                sentiment=classify_sentiment(text),
                collection_type=collection_type,
            )
        )
    return posts


def _dedupe_social_posts(posts: list[SocialPost]) -> list[SocialPost]:
    result: list[SocialPost] = []
    seen: set[str] = set()
    for post in posts:
        key = post.url or f"{post.author}:{post.published_at}:{post.text}"
        if key in seen:
            continue
        seen.add(key)
        result.append(post)
    return result


def build_social_adapter(name: str, client: HttpClient | None = None) -> SocialAdapter:
    if name == "manual":
        return ManualSocialAdapter()
    if name in {"x_api", "x"}:
        return XApiSocialAdapter(client)
    if name in {"", "disabled"}:
        return DisabledSocialAdapter()
    raise ValueError(f"unsupported social adapter: {name}")


def collect_social_posts(sources: Mapping[str, Any], client: HttpClient | None = None) -> tuple[list[SocialPost], list[str]]:
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
        if adapter_name in {"", "disabled"}:
            continue
        adapter = build_social_adapter(adapter_name, client)
        collected, adapter_warnings = adapter.collect(platform, config)
        posts.extend(collected)
        warnings.extend(adapter_warnings)
    return posts, warnings
