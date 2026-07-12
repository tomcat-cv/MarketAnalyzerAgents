from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .collectors import collect_rss_items
from .collectors_core import HttpClient, resolve_research_window
from .config import load_json, load_settings, resolve_path
from .evidence import EvidenceItem, configured_portfolio_holdings, dedupe_evidence
from .intraday import MarketDataProviderError, fetch_market_data
from .market_calendar import calendar_from_settings, market_status
from .market_sentiment import collect_market_sentiment
from .openai_runner import run_openai
from .portfolio_snapshots import normalize_holding
from .social_adapters import SocialPost, collect_social_posts
from .writer import markdown_to_html, write_json, write_json_atomic, write_text
from .zhipu_runner import run_zhipu


MARKETS = ("a_share", "us_equities")
REPORT_SCHEDULES = ("08:00", "14:00", "20:00")
SERVICE_ERROR_LIMIT = 8


@dataclass
class OfficialItem:
    title: str
    source: str
    published_at: str
    url: str
    summary: str
    topics: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)


@dataclass
class ContentPack:
    official: list[OfficialItem]
    social_posts: list[SocialPost]
    source_warnings: list[str]


@dataclass
class MarketQuoteRow:
    market: str
    symbol: str
    name: str
    price: float
    previous_close: float | None
    change_pct: float | None
    observed_at: str
    metrics: Mapping[str, float | None]


def beijing_now(settings: Mapping[str, Any]) -> datetime:
    return datetime.now(ZoneInfo(str(settings.get("timezone", "Asia/Shanghai"))))


def sources_path(root: Path, settings: Mapping[str, Any]) -> Path:
    return resolve_path(root, settings.get("sources_path", "config/sources.json"))


def read_sources(root: Path, settings: Mapping[str, Any]) -> dict[str, Any]:
    value = load_json(sources_path(root, settings), {})
    return dict(value) if isinstance(value, Mapping) else {}


def configured_topics(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for holding in configured_portfolio_holdings(sources):
        ticker = str(holding.get("ticker", "")).strip()
        if not ticker:
            continue
        topic_id = f"holding:{str(holding.get('market', '')).strip()}:{ticker.upper()}"
        if topic_id in seen:
            continue
        keywords = _holding_keywords(holding)
        topics.append(
            {
                "id": topic_id,
                "name": str(holding.get("company") or ticker).strip(),
                "keywords": keywords,
                "source": "holding",
                "market": str(holding.get("market", "")),
                "ticker": ticker,
            }
        )
        seen.add(topic_id)
    raw_topics = sources.get("focus_topics", [])
    if not isinstance(raw_topics, list):
        return topics
    for item in raw_topics:
        if not isinstance(item, Mapping):
            continue
        topic_id = str(item.get("id", item.get("name", ""))).strip()
        if not topic_id or topic_id in seen:
            continue
        seen.add(topic_id)
        keywords = _string_list(item.get("keywords", []))
        if not keywords:
            for segment in item.get("segments", []):
                if isinstance(segment, Mapping):
                    keywords.extend(_string_list(segment.get("topics", [])))
        topics.append(
            {
                "id": topic_id,
                "name": str(item.get("name", topic_id)).strip() or topic_id,
                "keywords": keywords,
                "source": "custom",
            }
        )
    return topics


def write_sources(root: Path, settings: Mapping[str, Any], sources: Mapping[str, Any]) -> Path:
    return write_json_atomic(sources_path(root, settings), dict(sources))


def analysis_dir(root: Path, settings: Mapping[str, Any], key: str) -> Path:
    state = settings.get("state", {})
    if not isinstance(state, Mapping):
        state = {}
    base = resolve_path(root, state.get("analysis_dir", "state/analysis"))
    return base / key


def service_status_path(root: Path, settings: Mapping[str, Any]) -> Path:
    state = settings.get("state", {})
    if not isinstance(state, Mapping):
        state = {}
    base = resolve_path(root, state.get("analysis_dir", "state/analysis")).parent
    return base / "service_status.json"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        pieces = re.split(r"[\n,，]+", value)
    elif isinstance(value, list):
        pieces = [str(item) for item in value]
    else:
        return []
    return [item.strip() for item in pieces if item.strip()]


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _holding_keywords(holding: Mapping[str, Any]) -> list[str]:
    values = [
        str(holding.get("ticker", "")),
        str(holding.get("symbol", "")),
        str(holding.get("company", "")),
        *_string_list(holding.get("themes", [])),
    ]
    return _dedupe_strings(values)


def configured_social_keywords(sources: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for holding in configured_portfolio_holdings(sources):
        values.extend(_holding_keywords(holding))
    for topic in configured_topics(sources):
        values.append(str(topic.get("name", "")))
        values.extend(_string_list(topic.get("keywords", [])))
    return _dedupe_strings(values)


def _normalize_social_sources(payload_value: Any, existing_value: Any, sources: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    payload_social = payload_value if isinstance(payload_value, Mapping) else {}
    existing_social = existing_value if isinstance(existing_value, Mapping) else {}
    keywords = configured_social_keywords(sources)
    result: dict[str, dict[str, Any]] = {}
    for platform in ("x", "xiaohongshu"):
        incoming = payload_social.get(platform, {})
        existing = existing_social.get(platform, {})
        if not isinstance(incoming, Mapping):
            incoming = {}
        if not isinstance(existing, Mapping):
            existing = {}
        adapter = str(incoming.get("adapter") or existing.get("adapter") or "disabled").strip()
        if adapter == "manual":
            adapter = "disabled"
        result[platform] = {
            "enabled": bool(incoming.get("enabled", existing.get("enabled", True))),
            "adapter": adapter or "disabled",
            "accounts": _string_list(incoming.get("accounts", existing.get("accounts", []))),
            "keywords": keywords,
        }
        for key in (
            "api_base",
            "api_key_env",
            "query_type",
            "include_replies",
            "exclude_replies",
            "exclude_retweets",
            "language",
            "account_max_results_per_account",
            "keyword_max_results",
            "max_results",
            "query",
            "request_interval_seconds",
        ):
            if key in incoming:
                result[platform][key] = incoming[key]
            elif key in existing:
                result[platform][key] = existing[key]
    return result


def _time_list(value: Any) -> list[str]:
    result = []
    for item in _string_list(value):
        if not re.fullmatch(r"[0-2]\d:[0-5]\d", item):
            raise ValueError("time values must use HH:MM")
        hour = int(item.split(":", 1)[0])
        if hour > 23:
            raise ValueError("time values must use HH:MM")
        if item not in result:
            result.append(item)
    return result


def _model_configuration(settings: Mapping[str, Any]) -> dict[str, Any]:
    backend = str(settings.get("backend", "zhipu"))
    openai_settings = settings.get("openai", {})
    zhipu_settings = settings.get("zhipu", {})
    intraday = settings.get("intraday_agents", {})
    if not isinstance(openai_settings, Mapping):
        openai_settings = {}
    if not isinstance(zhipu_settings, Mapping):
        zhipu_settings = {}
    if not isinstance(intraday, Mapping):
        intraday = {}
    active = settings.get(backend, {})
    if not isinstance(active, Mapping):
        active = {}
    return {
        "backend": backend,
        "model": str(active.get("model") or settings.get("model", "")),
        "openai_model": str(openai_settings.get("model", "")),
        "zhipu_model": str(zhipu_settings.get("model", "")),
        "openai_api_key_set": bool(str(openai_settings.get("api_key", "")).strip()),
        "zhipu_api_key_set": bool(str(zhipu_settings.get("api_key", "")).strip()),
        "advice_backend": str(intraday.get("advice_backend", backend)),
        "debate_rounds": int(intraday.get("debate_rounds", 1)),
        "report_schedule": list(settings.get("report_schedule", REPORT_SCHEDULES)),
        "intraday_suggestion_interval_seconds": int(settings.get("intraday_suggestion_interval_seconds", 1800)),
    }


def update_model_configuration(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path = root / "config" / "settings.json"
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    backend = str(payload.get("backend", settings.get("backend", "zhipu"))).strip()
    if backend not in {"zhipu", "openai", "dry-run"}:
        raise ValueError("backend must be zhipu, openai, or dry-run")
    settings["backend"] = backend
    settings.setdefault("openai", {})
    settings.setdefault("zhipu", {})
    settings.setdefault("intraday_agents", {})
    if not isinstance(settings["openai"], dict) or not isinstance(settings["zhipu"], dict):
        raise ValueError("model provider settings must be JSON objects")
    if not isinstance(settings["intraday_agents"], dict):
        settings["intraday_agents"] = {}
    openai_model = str(payload.get("openai_model", "")).strip()
    zhipu_model = str(payload.get("zhipu_model", "")).strip()
    openai_api_key = str(payload.get("openai_api_key", "")).strip()
    zhipu_api_key = str(payload.get("zhipu_api_key", "")).strip()
    model = str(payload.get("model", "")).strip()
    if openai_model:
        settings["openai"]["model"] = openai_model
    if zhipu_model:
        settings["zhipu"]["model"] = zhipu_model
    if openai_api_key and openai_api_key != "__KEEP__":
        settings["openai"]["api_key"] = openai_api_key
    if zhipu_api_key and zhipu_api_key != "__KEEP__":
        settings["zhipu"]["api_key"] = zhipu_api_key
    if backend == "openai" and (model or openai_model):
        settings["openai"]["model"] = model or openai_model
        settings["model"] = model or openai_model
    if backend == "zhipu" and (model or zhipu_model):
        settings["zhipu"]["model"] = model or zhipu_model
        settings["model"] = model or zhipu_model
    advice_backend = str(payload.get("advice_backend", "")).strip()
    if advice_backend:
        if advice_backend not in {"zhipu", "openai", "dry-run"}:
            raise ValueError("advice_backend must be zhipu, openai, or dry-run")
        settings["intraday_agents"]["advice_backend"] = advice_backend
    if str(payload.get("debate_rounds", "")).strip():
        rounds = int(payload["debate_rounds"])
        if rounds < 1 or rounds > 3:
            raise ValueError("debate_rounds must be between 1 and 3")
        settings["intraday_agents"]["debate_rounds"] = rounds
    if "report_schedule" in payload:
        schedule = _time_list(payload.get("report_schedule", []))
        if not schedule:
            raise ValueError("report_schedule must contain at least one HH:MM time")
        settings["report_schedule"] = schedule
    if str(payload.get("intraday_suggestion_interval_seconds", "")).strip():
        interval = int(payload["intraday_suggestion_interval_seconds"])
        if interval < 60:
            raise ValueError("intraday_suggestion_interval_seconds must be at least 60")
        settings["intraday_suggestion_interval_seconds"] = interval
    write_json_atomic(path, settings)
    return _model_configuration(load_settings(root))


def upsert_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    if market not in MARKETS:
        raise ValueError("market must be a_share or us_equities")
    value = normalize_holding(market, payload)
    holdings = sources.setdefault("portfolios", {}).setdefault(market, {}).setdefault("holdings", [])
    if not isinstance(holdings, list):
        raise ValueError("holdings must be a list")
    for index, item in enumerate(holdings):
        if str(item.get("ticker", "")).strip().upper() == value["ticker"].upper():
            holdings[index] = value
            break
    else:
        holdings.append(value)
    write_sources(root, settings, sources)
    return value


def delete_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    ticker = str(payload.get("ticker", "")).strip().upper()
    if market not in MARKETS or not ticker:
        raise ValueError("market and ticker are required")
    holdings = sources.get("portfolios", {}).get(market, {}).get("holdings", [])
    if isinstance(holdings, list):
        sources["portfolios"][market]["holdings"] = [
            item for item in holdings if str(item.get("ticker", "")).strip().upper() != ticker
        ]
    write_sources(root, settings, sources)
    return {"market": market, "ticker": ticker, "deleted": True}


def upsert_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    value = {
        "id": topic_id,
        "name": str(payload.get("name", topic_id)).strip() or topic_id,
        "keywords": _string_list(payload.get("keywords", [])),
    }
    topics = sources.setdefault("focus_topics", [])
    if not isinstance(topics, list):
        raise ValueError("focus_topics must be a list")
    for index, item in enumerate(topics):
        if isinstance(item, Mapping) and str(item.get("id", item.get("name", ""))).strip() == topic_id:
            topics[index] = value
            break
    else:
        topics.append(value)
    write_sources(root, settings, sources)
    return value


def delete_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    topics = sources.get("focus_topics", [])
    if isinstance(topics, list):
        sources["focus_topics"] = [
            item
            for item in topics
            if not isinstance(item, Mapping) or str(item.get("id", item.get("name", ""))).strip() != topic_id
        ]
    write_sources(root, settings, sources)
    return {"id": topic_id, "deleted": True}


def update_source_configuration(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    sources["official_sources"] = payload.get("official_sources", [])
    sources["social_sources"] = _normalize_social_sources(
        payload.get("social_sources", {}),
        sources.get("social_sources", {}),
        sources,
    )
    sources.pop("fear_greed", None)
    write_sources(root, settings, sources)
    return {
        "official_sources": sources["official_sources"],
        "social_sources": sources["social_sources"],
    }


def _configured_official_sources(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = sources.get("official_sources")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping) and item.get("enabled", True)]
    return []


def _collect_official(root: Path, settings: Mapping[str, Any], sources: Mapping[str, Any]) -> tuple[list[OfficialItem], list[str]]:
    collector_settings = settings.get("collectors", {})
    if not isinstance(collector_settings, Mapping):
        collector_settings = {}
    client = HttpClient(
        str(collector_settings.get("user_agent", "market-analyzer-agents/0.1")),
        int(collector_settings.get("timeout_seconds", 30)),
        int(collector_settings.get("max_retries", 2)),
        float(collector_settings.get("retry_backoff_seconds", 1.0)),
    )
    window_start, window_end, _ = resolve_research_window(settings)
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    for source in _configured_official_sources(sources):
        if str(source.get("type", "rss")) != "rss":
            continue
        try:
            rss_items = collect_rss_items(
                client=client,
                feed=source,
                cutoff=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            warnings.append(f"{source.get('name', source.get('url', 'official source'))}: {exc}")
            continue
        for item in rss_items:
            items.append(item)
    max_items = int(collector_settings.get("max_evidence_items", 200))
    return [
        OfficialItem(
            title=item.title,
            source=item.source_name,
            published_at=item.published_at,
            url=item.url,
            summary=item.content,
            topics=item.matched_topics,
            tickers=item.matched_tickers,
        )
        for item in dedupe_evidence(items, max_items)
    ], warnings


def _collector_client(settings: Mapping[str, Any]) -> HttpClient:
    collector_settings = settings.get("collectors", {})
    if not isinstance(collector_settings, Mapping):
        collector_settings = {}
    return HttpClient(
        str(collector_settings.get("user_agent", "market-analyzer-agents/0.1")),
        int(collector_settings.get("timeout_seconds", 30)),
        int(collector_settings.get("max_retries", 2)),
        float(collector_settings.get("retry_backoff_seconds", 1.0)),
    )


def current_market_sentiment(settings: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    try:
        value = collect_market_sentiment(_collector_client(settings), settings.get("market_sentiment", {}))
        value["generated_at"] = beijing_now(settings).isoformat(timespec="seconds")
        return value, []
    except Exception as exc:
        return {
            "value": "",
            "label": "暂不可用",
            "status": "error",
            "error": str(exc),
            "components": [],
        }, [f"market_sentiment: {exc}"]


def _market_quote_row(market: str, symbol: str, name: str, data: Any) -> MarketQuoteRow:
    previous_close = data.quote.previous_close
    change_pct = None
    if previous_close:
        change_pct = round(((data.quote.price / previous_close) - 1) * 100, 4)
    return MarketQuoteRow(
        market=market,
        symbol=symbol,
        name=name,
        price=data.quote.price,
        previous_close=previous_close,
        change_pct=change_pct,
        observed_at=data.quote.observed_at,
        metrics=data.metrics,
    )


def collect_market_overview(
    settings: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> tuple[dict[str, list[MarketQuoteRow]], list[str]]:
    overview_settings = settings.get("market_overview", {})
    if not isinstance(overview_settings, Mapping):
        overview_settings = {}
    if overview_settings.get("enabled", True) is False:
        return {"indices": [], "holdings": []}, []
    client = _collector_client(settings)
    market_data_settings = settings.get("market_data", {})
    if not isinstance(market_data_settings, Mapping):
        market_data_settings = {}
    warnings: list[str] = []
    indices: list[MarketQuoteRow] = []
    configured_indices = overview_settings.get("indices", {})
    if not isinstance(configured_indices, Mapping):
        configured_indices = {}
    for market in MARKETS:
        raw_rows = configured_indices.get(market, [])
        if not isinstance(raw_rows, list):
            continue
        for item in raw_rows:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol", "")).strip()
            if not symbol:
                continue
            name = str(item.get("name", symbol)).strip() or symbol
            try:
                data = fetch_market_data(client, market, symbol, market_data_settings)
            except Exception as exc:
                warnings.append(f"market_overview index {market} {symbol}: {exc}")
                continue
            indices.append(_market_quote_row(market, symbol, name, data))
    holdings: list[MarketQuoteRow] = []
    for holding in configured_portfolio_holdings(sources):
        market = str(holding.get("market", ""))
        symbol = str(holding.get("symbol") or holding.get("ticker") or "").strip()
        if not market or not symbol:
            continue
        name = str(holding.get("company") or holding.get("ticker") or symbol).strip()
        try:
            data = fetch_market_data(client, market, symbol, market_data_settings)
        except Exception as exc:
            warnings.append(f"market_overview holding {market} {symbol}: {exc}")
            continue
        holdings.append(_market_quote_row(market, symbol, name, data))
    return {"indices": indices, "holdings": holdings}, warnings


def collect_content(root: Path, settings: Mapping[str, Any], sources: Mapping[str, Any]) -> ContentPack:
    official, official_warnings = _collect_official(root, settings, sources)
    social_sources = _normalize_social_sources(
        sources.get("social_sources", {}),
        sources.get("social_sources", {}),
        sources,
    )
    collection_sources = {**dict(sources), "social_sources": social_sources}
    social_posts, social_warnings = collect_social_posts(collection_sources, _collector_client(settings))
    return ContentPack(
        official=official,
        social_posts=social_posts,
        source_warnings=official_warnings + social_warnings,
    )


def social_summary(posts: list[SocialPost]) -> dict[str, Any]:
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    by_author: dict[str, list[dict[str, str]]] = {}
    for post in posts:
        sentiment = post.sentiment if post.sentiment in counts else "neutral"
        counts[sentiment] += 1
        if post.author:
            by_author.setdefault(post.author, []).append(
                {
                    "platform": post.platform,
                    "published_at": post.published_at,
                    "url": post.url,
                    "text": post.text[:240],
                    "sentiment": sentiment,
                    "collection_type": post.collection_type,
                }
            )
    total = sum(counts.values())
    dominant = max(counts, key=lambda key: counts[key]) if total else "neutral"
    return {"counts": counts, "total": total, "dominant": dominant, "by_author": by_author}


def _configured_social_accounts(sources: Mapping[str, Any]) -> dict[str, list[str]]:
    social = sources.get("social_sources", {})
    if not isinstance(social, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for platform, config in social.items():
        if isinstance(config, Mapping) and config.get("enabled", True):
            accounts = [item.lstrip("@") for item in _string_list(config.get("accounts", [])) if item.lstrip("@")]
            if accounts:
                result[str(platform)] = accounts
    return result


def _post_dict(post: SocialPost, limit: int = 700) -> dict[str, str]:
    return {
        "platform": post.platform,
        "author": post.author,
        "published_at": post.published_at,
        "url": post.url,
        "text": post.text[:limit],
        "sentiment": post.sentiment,
        "collection_type": post.collection_type,
    }


def _split_social_posts(
    posts: list[SocialPost],
    configured_accounts: Mapping[str, list[str]],
) -> tuple[list[SocialPost], dict[str, dict[str, list[SocialPost]]]]:
    configured_keys = {
        (platform, account.casefold())
        for platform, accounts in configured_accounts.items()
        for account in accounts
    }
    keyword_posts: list[SocialPost] = []
    account_posts: dict[str, dict[str, list[SocialPost]]] = {}
    for post in posts:
        author_key = post.author.lstrip("@").casefold()
        if post.collection_type == "account" or (post.platform, author_key) in configured_keys:
            platform_posts = account_posts.setdefault(post.platform, {})
            display_author = post.author or next(
                (
                    account
                    for account in configured_accounts.get(post.platform, [])
                    if account.casefold() == author_key
                ),
                "",
            )
            if display_author:
                platform_posts.setdefault(display_author, []).append(post)
            continue
        keyword_posts.append(post)
    return keyword_posts, account_posts


def _fallback_blogger_summary(author: str, platform: str, posts: list[SocialPost]) -> str:
    if not posts:
        return "本次采集没有返回可分析的新帖。"
    counts = social_summary(posts)["counts"]
    latest_lines = [
        f"- [{post.published_at or '时间未知'}]({post.url})：{post.text[:180]}"
        for post in posts[:3]
        if post.url
    ]
    return "\n".join(
        [
            f"{author}（{platform}）本次采集 {len(posts)} 条：积极 {counts['positive']}，消极 {counts['negative']}，中性 {counts['neutral']}。",
            *(latest_lines or [f"- 最新内容：{posts[0].text[:180]}"]),
        ]
    )


def _configured_blogger_inputs(
    configured_accounts: Mapping[str, list[str]],
    account_posts: Mapping[str, Mapping[str, list[SocialPost]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for platform, accounts in configured_accounts.items():
        posts_by_author = account_posts.get(platform, {})
        for account in accounts:
            posts: list[SocialPost] = []
            display_author = account
            for author, author_posts in posts_by_author.items():
                if author.casefold() == account.casefold():
                    display_author = author
                    posts = author_posts[:20]
                    break
            result.append(
                {
                    "platform": platform,
                    "account": account,
                    "author": display_author,
                    "post_count": len(posts),
                    "posts": [_post_dict(post, 900) for post in posts],
                    "sentiment_summary": social_summary(posts),
                }
            )
    return result


def _configured_blogger_markdown(summaries: list[Mapping[str, Any]]) -> str:
    if not summaries:
        return ""
    lines = ["", "## 四、社媒观点与舆情", "", "### 配置博主观点"]
    for item in summaries:
        author = str(item.get("author", "")).strip()
        platform = str(item.get("platform", "")).strip()
        post_count = int(item.get("post_count") or 0)
        summary = str(item.get("summary", "")).strip()
        if not summary:
            posts = item.get("posts", [])
            if isinstance(posts, list):
                summary = _fallback_blogger_summary(
                    author,
                    platform,
                    [
                        SocialPost(
                            platform=platform,
                            author=author,
                            published_at=str(post.get("published_at", "")),
                            url=str(post.get("url", "")),
                            text=str(post.get("text", "")),
                            sentiment=str(post.get("sentiment", "neutral")),
                            collection_type="account",
                        )
                        for post in posts
                        if isinstance(post, Mapping)
                    ],
                )
        lines.extend(
            [
                "",
                f"#### {author}（{platform}，{post_count} 条）",
                summary or "本次采集没有返回可分析的新帖。",
            ]
        )
    return "\n".join(lines)


def _ensure_configured_blogger_section(markdown: str, summaries: list[Mapping[str, Any]]) -> str:
    missing = [
        item
        for item in summaries
        if str(item.get("author", "")).strip()
        and str(item.get("author", "")).strip().casefold() not in markdown.casefold()
    ]
    if not missing:
        return markdown
    return markdown.rstrip() + "\n" + _configured_blogger_markdown(missing)


def _call_model(settings: Mapping[str, Any], *, system: str, user: str, backend: str | None = None) -> str:
    selected = backend or str(settings.get("backend", "zhipu"))
    if selected == "dry-run":
        return ""
    if selected == "openai":
        result, _ = run_openai(settings=settings, system=system, user=user)
        return result.text.strip()
    if selected == "zhipu":
        result, _ = run_zhipu(settings=settings, system=system, user=user)
        return result.text.strip()
    raise ValueError("backend must be zhipu, openai, or dry-run")


def _call_report_section(
    settings: Mapping[str, Any],
    warnings: list[str],
    *,
    section_name: str,
    system: str,
    payload: Mapping[str, Any],
    fallback: str,
    backend: str | None = None,
) -> str:
    try:
        markdown = _call_model(
            settings,
            system=system,
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            backend=backend,
        )
    except Exception as exc:
        warnings.append(f"model section {section_name}: {exc}")
        return fallback
    return markdown or fallback


def _format_change(value: float | None) -> str:
    if value is None:
        return "涨跌幅暂不可用"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _market_label(value: str) -> str:
    return {"a_share": "A 股", "us_equities": "美股"}.get(value, value)


def _quote_lines(rows: list[MarketQuoteRow], limit: int = 8) -> list[str]:
    return [
        (
            f"- {row.name}（{_market_label(row.market)} {row.symbol}）："
            f"{row.price:.2f}，较前收 {_format_change(row.change_pct)}，"
            f"1个月表现 {_format_change(row.metrics.get('period_change_pct') if row.metrics else None)}。"
        )
        for row in rows[:limit]
    ]


def _official_lines(items: list[OfficialItem], limit: int = 8) -> list[str]:
    return [
        f"- [{item.title}]({item.url})：{item.summary[:180]}"
        for item in items[:limit]
        if item.url and item.title
    ]


def _market_sentiment_text(market_sentiment: Mapping[str, Any]) -> str:
    if isinstance(market_sentiment, Mapping) and market_sentiment.get("value") not in (None, ""):
        return f"{market_sentiment.get('value')} / {market_sentiment.get('label', '')}".strip()
    return "市场情绪数据暂不可用；请检查行情、CBOE 或 FRED 数据源。"


def _fallback_market_section(market_overview: Mapping[str, list[MarketQuoteRow]], market_sentiment: Mapping[str, Any]) -> str:
    index_lines = _quote_lines(list(market_overview.get("indices", [])), 8)
    return "\n".join(
        [
            "## 一、市场整体概况",
            *(index_lines or ["- 指数行情暂不可用；请检查 market_data provider 或网络访问。"]),
            f"- 市场情绪：{_market_sentiment_text(market_sentiment)}",
        ]
    )


def _fallback_portfolio_section(market_overview: Mapping[str, list[MarketQuoteRow]], sources: Mapping[str, Any]) -> str:
    holding_lines = _quote_lines(list(market_overview.get("holdings", [])), 8)
    topic_lines = [
        f"- {topic.get('name', topic.get('id', '关注主题'))}：{', '.join(_string_list(topic.get('keywords', []))[:6])}"
        for topic in configured_topics(sources)
        if topic.get("source") != "holding"
    ]
    return "\n".join(
        [
            "## 二、持仓与关注主题",
            *(holding_lines or ["- 持仓行情暂不可用；当前报告仅使用配置的持仓和 Topic 作为新闻匹配上下文。"]),
            *(topic_lines or []),
        ]
    )


def _fallback_official_section(items: list[OfficialItem]) -> str:
    return "\n".join(
        [
            "## 三、官方资讯",
            *(_official_lines(items, 10) or ["- 当前窗口内没有新的官方链接进入报告。"]),
        ]
    )


def _fallback_social_section(
    keyword_posts: list[SocialPost],
    configured_bloggers: list[Mapping[str, Any]],
) -> str:
    sentiment = social_summary(keyword_posts)
    author_lines = []
    for author, posts in list(sentiment["by_author"].items())[:6]:
        latest = posts[0]
        author_lines.append(f"- {author}（{latest['platform']}，{latest['sentiment']}）：{latest['text']}")
    blogger_lines = []
    for blogger in configured_bloggers:
        author = str(blogger.get("author", blogger.get("account", ""))).strip()
        platform = str(blogger.get("platform", "")).strip()
        posts = blogger.get("posts", [])
        if isinstance(posts, list):
            converted_posts = [
                SocialPost(
                    platform=platform,
                    author=author,
                    published_at=str(post.get("published_at", "")),
                    url=str(post.get("url", "")),
                    text=str(post.get("text", "")),
                    sentiment=str(post.get("sentiment", "neutral")),
                    collection_type="account",
                )
                for post in posts
                if isinstance(post, Mapping)
            ]
        else:
            converted_posts = []
        blogger_lines.extend(["", f"#### {author}（{platform}，{len(converted_posts)} 条）", _fallback_blogger_summary(author, platform, converted_posts)])
    return "\n".join(
        [
            "## 四、社媒观点与舆情",
            "### 关键词舆情概览",
            f"- 帖子统计：积极 {sentiment['counts']['positive']}，消极 {sentiment['counts']['negative']}，中性 {sentiment['counts']['neutral']}；主导情绪 {sentiment['dominant']}。",
            *(author_lines or ["- 本次关键词采集没有返回可分析的新帖。"]),
            "",
            "### 配置博主观点",
            *(blogger_lines or ["- 本次没有配置博主帖子进入采集结果。"]),
        ]
    )


def _report_fallback(
    pack: ContentPack,
    sources: Mapping[str, Any],
    run_at: datetime,
    market_overview: Mapping[str, list[MarketQuoteRow]] | None = None,
) -> str:
    market_sentiment = sources.get("market_sentiment", {})
    overview = market_overview or {}
    configured_accounts = _configured_social_accounts(sources)
    keyword_posts, account_posts = _split_social_posts(pack.social_posts, configured_accounts)
    configured_bloggers = _configured_blogger_inputs(configured_accounts, account_posts)
    return "\n".join(
        [
            f"# 市场分析报告 {run_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            _fallback_market_section(overview, market_sentiment if isinstance(market_sentiment, Mapping) else {}),
            "",
            _fallback_portfolio_section(overview, sources),
            "",
            _fallback_official_section(pack.official),
            "",
            _fallback_social_section(keyword_posts, configured_bloggers),
        ]
    )


def generate_market_report(root: Path, *, slot: str | None = None, backend: str | None = None) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    run_at = beijing_now(settings)
    slot_value = slot or run_at.strftime("%H:%M")
    pack = collect_content(root, settings, sources)
    market_overview, overview_warnings = collect_market_overview(settings, sources)
    pack.source_warnings.extend(overview_warnings)
    market_sentiment, sentiment_warnings = current_market_sentiment(settings)
    pack.source_warnings.extend(sentiment_warnings)
    configured_accounts = _configured_social_accounts(sources)
    keyword_posts, account_posts = _split_social_posts(pack.social_posts, configured_accounts)
    configured_bloggers = _configured_blogger_inputs(configured_accounts, account_posts)
    market_payload = {
        "indices": [item.__dict__ for item in market_overview["indices"]],
        "market_sentiment": market_sentiment,
        "warnings": pack.source_warnings,
    }
    portfolio_payload = {
        "holdings": configured_portfolio_holdings(sources),
        "topics": configured_topics(sources),
        "holding_quotes": [item.__dict__ for item in market_overview["holdings"]],
        "official": [item.__dict__ for item in pack.official[:8]],
        "market_sentiment": market_sentiment,
    }
    official_payload = {"official": [item.__dict__ for item in pack.official[:12]]}
    social_payload = {
        "keyword_posts": [_post_dict(item) for item in keyword_posts[:20]],
        "keyword_summary": social_summary(keyword_posts),
        "configured_accounts": configured_accounts,
        "configured_bloggers": configured_bloggers,
    }
    market_section = _call_report_section(
        settings,
        pack.source_warnings,
        section_name="market_overview",
        system=(
            "你是市场概况分析师。只基于输入的指数行情和市场情绪写中文 Markdown。"
            "必须以“## 一、市场整体概况”开头。"
            "把市场情绪指标写在本章节中；不要写社媒、官方资讯或博主观点。"
            "内容要分层清晰，优先说明 A 股和美股分化、风险偏好和需要注意的市场状态。"
        ),
        payload=market_payload,
        fallback=_fallback_market_section(market_overview, market_sentiment),
        backend=backend,
    )
    portfolio_section = _call_report_section(
        settings,
        pack.source_warnings,
        section_name="portfolio_topics",
        system=(
            "你是持仓与主题分析师。只基于输入的持仓、关注主题、持仓行情和官方资讯上下文写中文 Markdown。"
            "必须以“## 二、持仓与关注主题”开头。"
            "说明持仓和关注主题受到当前市场、资讯和主题变化的影响；不要写社媒关键词舆情或配置博主观点。"
        ),
        payload=portfolio_payload,
        fallback=_fallback_portfolio_section(market_overview, sources),
        backend=backend,
    )
    official_section = _call_report_section(
        settings,
        pack.source_warnings,
        section_name="official_news",
        system=(
            "你是官方资讯编辑。只基于输入的官方资讯写中文 Markdown。"
            "必须以“## 三、官方资讯”开头。"
            "官方资讯必须使用可读标题链接，不输出裸 URL；没有官方资讯时直接说明当前未采集到新的官方链接。"
        ),
        payload=official_payload,
        fallback=_fallback_official_section(pack.official),
        backend=backend,
    )
    social_section = _call_report_section(
        settings,
        pack.source_warnings,
        section_name="social",
        system=(
            "你是社媒观点分析师。只基于输入的社媒数据写中文 Markdown。"
            "必须以“## 四、社媒观点与舆情”开头，并且必须包含两个二级小节："
            "### 关键词舆情概览 和 ### 配置博主观点。"
            "关键词舆情概览只总结 keyword_posts 与 keyword_summary。"
            "配置博主观点必须按 configured_bloggers 中的每个配置账号逐个输出分析结果；即使某个账号没有返回帖子，也要说明本次未采集到可分析的新帖。"
            "不要把关键词样本作者当成配置博主；不要引入输入以外的平台数据。"
        ),
        payload=social_payload,
        fallback=_fallback_social_section(keyword_posts, configured_bloggers),
        backend=backend,
    )
    markdown = "\n\n".join(
        [
            f"# 市场分析报告 {run_at.strftime('%Y-%m-%d %H:%M')}",
            market_section.strip(),
            portfolio_section.strip(),
            official_section.strip(),
            social_section.strip(),
        ]
    )
    markdown = _ensure_configured_blogger_section(markdown, configured_bloggers)
    report_id = f"{run_at.strftime('%Y%m%d-%H%M%S')}-{slot_value.replace(':', '')}"
    json_path = analysis_dir(root, settings, "reports") / f"{report_id}.json"
    html_path = analysis_dir(root, settings, "reports") / f"{report_id}.html"
    result = {
        "id": report_id,
        "slot": slot_value,
        "generated_at": run_at.isoformat(timespec="seconds"),
        "title": f"{run_at.strftime('%Y-%m-%d')} {slot_value} 市场分析报告",
        "markdown": markdown,
        "html_path": str(html_path),
        "official_count": len(pack.official),
        "social_count": len(pack.social_posts),
        "market_overview": {
            "indices": [item.__dict__ for item in market_overview["indices"]],
            "holdings": [item.__dict__ for item in market_overview["holdings"]],
        },
        "warnings": pack.source_warnings,
    }
    write_json(json_path, result)
    write_text(html_path, markdown_to_html(markdown, result["title"]))
    return result


def generate_intraday_suggestion(root: Path, *, backend: str | None = None) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    run_at = beijing_now(settings)
    intraday_settings = settings.get("intraday_agents", {})
    if not isinstance(intraday_settings, Mapping):
        intraday_settings = {}
    debate_rounds = int(intraday_settings.get("debate_rounds", 1))
    holdings = configured_portfolio_holdings(sources)
    quote_rows: list[dict[str, Any]] = []
    quote_errors: list[str] = []
    collector_settings = settings.get("collectors", {})
    if not isinstance(collector_settings, Mapping):
        collector_settings = {}
    client = HttpClient(
        str(collector_settings.get("user_agent", "market-analyzer-agents/0.1")),
        int(collector_settings.get("timeout_seconds", 30)),
        int(collector_settings.get("max_retries", 2)),
        float(collector_settings.get("retry_backoff_seconds", 1.0)),
    )
    for holding in holdings:
        market = str(holding.get("market", ""))
        symbol = str(holding.get("symbol") or holding.get("ticker") or "").strip()
        try:
            data = fetch_market_data(client, market, symbol, settings.get("market_data", {}))
        except (MarketDataProviderError, ValueError) as exc:
            quote_errors.append(f"{market} {symbol}: {exc}")
            continue
        quote_rows.append(
            {
                "market": market,
                "symbol": symbol,
                "price": data.quote.price,
                "previous_close": data.quote.previous_close,
                "observed_at": data.quote.observed_at,
                "metrics": data.metrics,
            }
        )
    pack = collect_content(root, settings, sources)
    market_sentiment, sentiment_warnings = current_market_sentiment(settings)
    pack.source_warnings.extend(sentiment_warnings)
    payload = {
        "holdings": holdings,
        "quotes": quote_rows,
        "quote_errors": quote_errors,
        "official": [item.__dict__ for item in pack.official[:12]],
        "social_summary": social_summary(pack.social_posts),
        "market_sentiment": market_sentiment,
        "debate_rounds": debate_rounds,
    }
    system = (
        "你是盘中多 agent 讨论的主持人。用市场分析师、新闻分析师、多头研究员、空头研究员、"
        f"风险经理、组合经理六个职责形成 {debate_rounds} 轮简短讨论，最后给出可执行的持仓级操作建议。"
        "不要输出无意义免责声明；建议必须有触发条件、风险点、观察位。"
    )
    try:
        markdown = _call_model(
            settings,
            system=system,
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            backend=backend or str(intraday_settings.get("advice_backend", settings.get("backend", "zhipu"))),
        )
    except Exception as exc:
        pack.source_warnings.append(f"model: {exc}")
        markdown = (
            f"# 盘中操作建议 {run_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            "## 组合经理结论\n"
            "维持既有仓位纪律，优先处理价格异动和已验证新闻共同指向的标的。\n\n"
            "## 观察位\n"
            + "\n".join(f"- {row['symbol']}：现价 {row['price']}，前收 {row.get('previous_close')}" for row in quote_rows[:12])
        )
    suggestion_id = run_at.strftime("%Y%m%d-%H%M%S")
    path = analysis_dir(root, settings, "suggestions") / f"{suggestion_id}.json"
    result = {
        "id": suggestion_id,
        "generated_at": run_at.isoformat(timespec="seconds"),
        "title": f"{run_at.strftime('%Y-%m-%d %H:%M')} 盘中操作建议",
        "markdown": markdown,
        "quote_count": len(quote_rows),
        "warnings": pack.source_warnings + quote_errors,
    }
    write_json(path, result)
    return result


def list_reports(root: Path, settings: Mapping[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    directory = analysis_dir(root, settings, "reports")
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    reports = []
    for path in files[:limit]:
        item = load_json(path, {})
        if isinstance(item, Mapping):
            copied = dict(item)
            copied["url"] = "/analysis/reports/" + urllib.parse.quote(path.with_suffix(".html").name)
            reports.append(copied)
    return reports


def list_suggestions(root: Path, settings: Mapping[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    directory = analysis_dir(root, settings, "suggestions")
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    result = []
    for path in files[:limit]:
        item = load_json(path, {})
        if isinstance(item, Mapping):
            result.append(dict(item))
    return result


def dashboard_state(root: Path) -> dict[str, Any]:
    settings = load_settings(root)
    sources = read_sources(root, settings)
    markets: dict[str, Any] = {}
    for market in MARKETS:
        market_settings = settings.get("markets", {}).get(market, {})
        if not isinstance(market_settings, Mapping):
            market_settings = {}
        status = market_status(
            market,
            holidays=market_settings.get("holidays", []),
            extra_open_dates=market_settings.get("extra_open_dates", []),
            early_closes=market_settings.get("early_closes", {}),
            calendar=calendar_from_settings(market_settings, root=root),
        )
        markets[market] = {"state": status.state, "as_of_beijing": status.as_of_beijing.isoformat(timespec="seconds")}
    reports = list_reports(root, settings)
    suggestions = list_suggestions(root, settings)
    social = sources.get("social_sources", {})
    official = _configured_official_sources(sources)
    social_sources = _normalize_social_sources(social, social, sources)
    topics = configured_topics(sources)
    market_sentiment, sentiment_warnings = current_market_sentiment(settings)
    service_status = load_json(service_status_path(root, settings), {})
    if not isinstance(service_status, Mapping):
        service_status = {}
    return {
        "generated_at": beijing_now(settings).isoformat(timespec="seconds"),
        "display_timezone": str(settings.get("timezone", "Asia/Shanghai")),
        "markets": markets,
        "holdings": configured_portfolio_holdings(sources),
        "focus_topics": topics,
        "custom_focus_topics": [item for item in topics if item.get("source") != "holding"],
        "configuration": _model_configuration(settings),
        "official_sources": official,
        "social_sources": social_sources,
        "social_keywords": configured_social_keywords(sources),
        "market_sentiment": market_sentiment,
        "warnings": sentiment_warnings,
        "service_status": dict(service_status),
        "latest_report": reports[0] if reports else None,
        "reports": reports,
        "suggestions": suggestions,
        "report_schedule": list(settings.get("report_schedule", REPORT_SCHEDULES)),
    }


def _open_market_suggestion_interval(settings: Mapping[str, Any], market_states: Mapping[str, Any]) -> int | None:
    intervals: list[int] = []
    markets = settings.get("markets", {})
    if not isinstance(markets, Mapping):
        markets = {}
    for market, state in market_states.items():
        if not isinstance(state, Mapping) or state.get("state") != "open":
            continue
        market_settings = markets.get(market, {})
        if not isinstance(market_settings, Mapping):
            market_settings = {}
        intervals.append(int(market_settings.get("poll_interval_seconds", settings.get("intraday_suggestion_interval_seconds", 1800))))
    if not intervals:
        return None
    global_interval = int(settings.get("intraday_suggestion_interval_seconds", 1800))
    return max(60, min([global_interval, *intervals]))


def service_loop(root: Path, *, tick_seconds: int = 30, run_on_start: bool = False) -> None:
    last_report_key = ""
    last_suggestion_key = ""
    last_report_result: dict[str, Any] | None = None
    recent_errors: list[dict[str, str]] = []

    def record_error(settings: Mapping[str, Any], now: datetime, task: str, exc: Exception) -> None:
        nonlocal recent_errors
        error = {
            "task": task,
            "at": now.isoformat(timespec="seconds"),
            "type": type(exc).__name__,
            "message": str(exc),
        }
        recent_errors = ([error] + recent_errors)[:SERVICE_ERROR_LIMIT]
        print(f"market-analyzer service {task} failed at {error['at']}: {error['type']}: {error['message']}", file=sys.stderr, flush=True)
        write_json_atomic(
            service_status_path(root, settings),
            {
                "pid": os.getpid(),
                "last_seen_at": now.isoformat(timespec="seconds"),
                "report_schedule": settings.get("report_schedule", REPORT_SCHEDULES),
                "last_report_key": last_report_key,
                "last_report_id": last_report_result.get("id") if last_report_result else "",
                "last_error": error,
                "recent_errors": recent_errors,
                "tick_seconds": tick_seconds,
            },
        )

    if run_on_start:
        settings = load_settings(root)
        now = beijing_now(settings)
        try:
            last_report_result = generate_market_report(root)
        except Exception as exc:
            record_error(settings, now, "run_on_start_report", exc)
    while True:
        settings = load_settings(root)
        now = beijing_now(settings)
        schedule = settings.get("report_schedule", REPORT_SCHEDULES)
        if not isinstance(schedule, list):
            schedule = list(REPORT_SCHEDULES)
        write_json_atomic(
            service_status_path(root, settings),
            {
                "pid": os.getpid(),
                "last_seen_at": now.isoformat(timespec="seconds"),
                "report_schedule": schedule,
                "last_report_key": last_report_key,
                "last_report_id": last_report_result.get("id") if last_report_result else "",
                "last_error": recent_errors[0] if recent_errors else None,
                "recent_errors": recent_errors,
                "tick_seconds": tick_seconds,
            },
        )
        current_minute = now.strftime("%H:%M")
        current_day_key = now.strftime("%Y-%m-%d")
        if current_minute in schedule and last_report_key != f"{current_day_key} {current_minute}":
            try:
                last_report_result = generate_market_report(root, slot=current_minute)
                last_report_key = f"{current_day_key} {current_minute}"
                settings = load_settings(root)
                write_json_atomic(
                    service_status_path(root, settings),
                    {
                        "pid": os.getpid(),
                        "last_seen_at": beijing_now(settings).isoformat(timespec="seconds"),
                        "report_schedule": schedule,
                        "last_report_key": last_report_key,
                        "last_report_id": last_report_result.get("id"),
                        "last_error": recent_errors[0] if recent_errors else None,
                        "recent_errors": recent_errors,
                        "tick_seconds": tick_seconds,
                    },
                )
            except Exception as exc:
                record_error(settings, now, "scheduled_report", exc)
        try:
            market_states = dashboard_state(root)["markets"]
            suggestion_interval = _open_market_suggestion_interval(settings, market_states)
            suggestion_key = now.strftime("%Y-%m-%d %H:%M")
            if suggestion_interval is not None and suggestion_key != last_suggestion_key:
                seconds_since_hour = now.minute * 60 + now.second
                if seconds_since_hour % suggestion_interval < tick_seconds:
                    generate_intraday_suggestion(root)
                    last_suggestion_key = suggestion_key
        except Exception as exc:
            record_error(settings, now, "intraday_suggestion", exc)
        time.sleep(max(1, tick_seconds))
