from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


URL_RE = re.compile(r"https?://[^\s)>\]\[\"]+")
NUMBER_RE = re.compile(r"(?<![0-9])\d[\d,]*(?:\.\d+)?%?(?![0-9])")
FORM_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])(?:\d{1,3}-[A-Z][A-Z0-9]*|[A-Z]{1,5}-\d+[A-Z0-9]*|"
    r"\d{1,4}B\d+|\d{1,3}[A-Z])(?![A-Z0-9])",
    re.IGNORECASE,
)
COLLECTION_GAP_RE = re.compile(
    r"(?:本)?窗口(?:期)?内(?:没有任何|没有|无)[^。；]*(?:事件|信息|新闻|动态|内容)"
)


@dataclass
class EvidenceItem:
    id: str
    title: str
    published_at: str
    source_name: str
    source_type: str
    url: str
    content: str
    matched_topics: List[str] = field(default_factory=list)
    matched_tickers: List[str] = field(default_factory=list)
    evidence_level: str = "summary"


@dataclass
class SourceCoverage:
    name: str
    category: str
    status: str
    item_count: int = 0
    detail: str = ""


@dataclass
class EvidencePack:
    retrieved_at: str
    lookback_hours: int
    items: List[EvidenceItem] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    window_start: str = ""
    window_end: str = ""
    window_mode: str = "rolling_hours"
    timezone: str = "UTC"
    coverage: List[SourceCoverage] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "retrieved_at": self.retrieved_at,
            "lookback_hours": self.lookback_hours,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_mode": self.window_mode,
            "timezone": self.timezone,
            "items": [asdict(item) for item in self.items],
            "errors": list(self.errors),
            "coverage": [asdict(entry) for entry in self.coverage],
        }


@dataclass
class PortfolioAction:
    ticker: str
    action: str
    confidence: str
    rationale: str
    evidence_ids: List[str]
    watch_for: str


@dataclass
class ModelBrief:
    summaries: Dict[str, str]
    analyses: Dict[str, str]
    portfolio_actions: List[PortfolioAction]


def _normalized_numbers(value: str) -> set[str]:
    normalized: set[str] = set()
    without_form_tokens = FORM_TOKEN_RE.sub("", value)
    for token in NUMBER_RE.findall(without_form_tokens):
        suffix = "%" if token.endswith("%") else ""
        raw = token.rstrip("%").replace(",", "")
        try:
            decimal_value = Decimal(raw)
            number = format(decimal_value.normalize(), "f")
        except InvalidOperation:
            continue
        normalized.add(number + suffix)
        decimal_part = raw.partition(".")[2]
        if decimal_part and len(decimal_part) > 2:
            rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            normalized.add(format(rounded.normalize(), "f") + suffix)
        if suffix and decimal_part:
            rounded_percent = decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            normalized.add(format(rounded_percent.normalize(), "f") + suffix)
        if not suffix and abs(decimal_value) >= Decimal("10000"):
            wan_value = decimal_value / Decimal("10000")
            wan_rounded = wan_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            normalized.add(format(wan_rounded.normalize(), "f"))
            wan_integer = wan_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            normalized.add(format(wan_integer.normalize(), "f"))
    return normalized


def _normalize_collection_gap_language(value: str) -> str:
    return COLLECTION_GAP_RE.sub(
        "本窗口内，配置的可靠信源未采集到该持仓的直接相关条目",
        value,
    )


def configured_holdings(sources: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_holdings = sources.get("portfolios", {}).get("us_equities", {}).get("holdings", [])
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raw_holdings = sources.get("portfolio", {}).get("holdings", [])
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raw_holdings = sources.get("watchlist", {}).get("us_equities", [])

    holdings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_holdings:
        if not isinstance(entry, Mapping):
            continue
        ticker = str(entry.get("ticker", "")).upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        holdings.append(
            {
                "ticker": ticker,
                "company": str(entry.get("company", ticker)).strip() or ticker,
                "themes": [str(value) for value in entry.get("themes", [])],
            }
        )
    return holdings


def configured_a_share_holdings(sources: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_holdings = sources.get("portfolios", {}).get("a_share", {}).get("holdings", [])
    if not isinstance(raw_holdings, list):
        return []

    holdings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_holdings:
        if not isinstance(entry, Mapping):
            continue
        ticker = str(entry.get("ticker", "")).strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        holdings.append(
            {
                "ticker": ticker,
                "company": str(entry.get("company", ticker)).strip() or ticker,
                "themes": [str(value) for value in entry.get("themes", [])],
            }
        )
    return holdings


def canonical_url(value: str) -> str:
    split = urlsplit(value.strip())
    return urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path, split.query, ""))


def dedupe_evidence(items: Iterable[EvidenceItem], max_items: int) -> List[EvidenceItem]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    deduped: List[EvidenceItem] = []

    newest_first = sorted(items, key=lambda item: item.published_at, reverse=True)
    level_priority = {"summary": 0, "metadata_only": 1, "title_only": 2}
    source_priority = {
        "primary": 0,
        "reputable_reporting": 1,
        "market_data_aggregator": 2,
        "news_aggregator": 3,
    }
    ranked = sorted(
        newest_first,
        key=lambda item: (
            level_priority.get(item.evidence_level, 3),
            source_priority.get(item.source_type, 4),
        ),
    )
    for item in ranked:
        url = canonical_url(item.url)
        title = re.sub(r"\s+", " ", item.title).strip().lower()
        if not url or url in seen_urls or title in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title)
        deduped.append(item)
        if len(deduped) >= max_items:
            break

    for index, item in enumerate(deduped, start=1):
        item.id = f"EVID-{index:03d}"
    return deduped


def new_evidence_pack(
    lookback_hours: int,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    window_mode: str = "rolling_hours",
    timezone_name: str = "UTC",
) -> EvidencePack:
    return EvidencePack(
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        lookback_hours=lookback_hours,
        window_start=window_start.isoformat(timespec="seconds") if window_start else "",
        window_end=window_end.isoformat(timespec="seconds") if window_end else "",
        window_mode=window_mode,
        timezone=timezone_name,
    )


def evidence_pack_markdown(pack: EvidencePack, max_content_chars: int = 1600) -> str:
    parts = [
        "# Verified Evidence Pack",
        "",
        f"- Retrieved at: {pack.retrieved_at}",
        f"- Lookback hours: {pack.lookback_hours}",
        f"- Window mode: {pack.window_mode}",
        f"- Timezone: {pack.timezone}",
        f"- Window start: {pack.window_start or '(not recorded)'}",
        f"- Window end: {pack.window_end or '(not recorded)'}",
        f"- Verified items: {len(pack.items)}",
        "",
        "Only the evidence below may be used as factual input. Source configuration,",
        "watchlists, and prior model knowledge are not evidence.",
    ]
    if pack.coverage:
        parts.extend(["", "## Collector Coverage"])
        for entry in pack.coverage:
            detail = f" Detail: {entry.detail}" if entry.detail else ""
            parts.append(
                f"- {entry.name} ({entry.category}): {entry.status}; "
                f"items={entry.item_count}.{detail}"
            )
    if pack.errors:
        parts.extend(["", "## Collection Warnings"])
        parts.extend(f"- {error}" for error in pack.errors)

    parts.extend(["", "## Evidence Items"])
    for item in pack.items:
        content = re.sub(r"\s+", " ", item.content).strip()[:max_content_chars]
        parts.extend(
            [
                "",
                f"### {item.id}: {item.title}",
                f"- Source: {item.source_name} ({item.source_type})",
                f"- Published: {item.published_at}",
                f"- URL: {item.url}",
                f"- Evidence level: {item.evidence_level}",
                f"- Matched topics: {', '.join(item.matched_topics) or '(none)'}",
                f"- Matched tickers: {', '.join(item.matched_tickers) or '(none)'}",
                f"- Evidence: {content}",
            ]
        )
    return "\n".join(parts).strip() + "\n"


def filter_evidence_pack(pack: EvidencePack, levels: set[str]) -> EvidencePack:
    return EvidencePack(
        retrieved_at=pack.retrieved_at,
        lookback_hours=pack.lookback_hours,
        items=[item for item in pack.items if item.evidence_level in levels],
        errors=[],
        window_start=pack.window_start,
        window_end=pack.window_end,
        window_mode=pack.window_mode,
        timezone=pack.timezone,
        coverage=[],
    )


def evidence_review_queue_markdown(pack: EvidencePack) -> str:
    review_items = [item for item in pack.items if item.evidence_level != "summary"]
    if not review_items:
        return ""
    parts = [
        "## Evidence Review Queue",
        "",
        "以下条目只有标题或提交元数据，未交给大模型分析。请打开原始来源核验详情：",
        "",
    ]
    for item in review_items:
        parts.append(f"- **{item.id} · {item.title}** — [{item.source_name}]({item.url})")
    return "\n".join(parts).strip() + "\n"


def _extract_model_payload(text: str) -> Mapping[str, Any]:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model output did not contain a JSON object.")

    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output was not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Model output JSON must be an object.")
    return payload


def _validate_model_text(
    *,
    evidence_id: str,
    field_name: str,
    value: str,
    evidence_item: EvidenceItem,
    max_chars: int,
    extra_number_context: str = "",
) -> None:
    if not value:
        raise ValueError(f"Model returned an empty {field_name} for {evidence_id}.")
    if URL_RE.search(value):
        raise ValueError(f"Model {field_name} for {evidence_id} contains a URL.")
    if len(value) > max_chars:
        raise ValueError(f"Model {field_name} for {evidence_id} exceeds {max_chars} characters.")
    allowed_numbers = _normalized_numbers(
        f"{evidence_item.title} {evidence_item.published_at} {evidence_item.content} "
        f"{extra_number_context}"
    )
    introduced_numbers = sorted(_normalized_numbers(value) - allowed_numbers)
    if introduced_numbers:
        raise ValueError(
            f"Model {field_name} for {evidence_id} introduced unsupported numbers: "
            f"{', '.join(introduced_numbers)}"
        )


def _parse_summary_entries(
    entries: Any,
    pack: EvidencePack,
    *,
    require_analysis: bool = False,
) -> tuple[Dict[str, str], Dict[str, str]]:
    if not isinstance(entries, list):
        raise ValueError("Model output must contain a summaries list.")

    allowed_ids = {item.id for item in pack.items}
    pack_number_context = " ".join(
        f"{item.id} {item.title} {item.published_at} {item.content}" for item in pack.items
    )
    summaries: Dict[str, str] = {}
    analyses: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Every model summary entry must be an object.")
        expected_keys = {"evidence_id", "summary", "analysis"} if require_analysis else {"evidence_id", "summary"}
        if set(entry.keys()) != expected_keys:
            if require_analysis:
                raise ValueError(
                    "Every model summary entry must contain only evidence_id, summary, and analysis."
                )
            raise ValueError("Every model summary entry must contain only evidence_id and summary.")
        evidence_id = str(entry.get("evidence_id", "")).strip()
        summary = re.sub(r"\s+", " ", str(entry.get("summary", ""))).strip()
        if evidence_id not in allowed_ids:
            raise ValueError(f"Model returned an unknown evidence ID: {evidence_id or '(empty)'}")
        if evidence_id in summaries:
            raise ValueError(f"Model returned duplicate evidence ID: {evidence_id}")
        evidence_item = next(item for item in pack.items if item.id == evidence_id)
        _validate_model_text(
            evidence_id=evidence_id,
            field_name="summary",
            value=summary,
            evidence_item=evidence_item,
            max_chars=800,
        )
        summaries[evidence_id] = summary
        if require_analysis:
            analysis = re.sub(r"\s+", " ", str(entry.get("analysis", ""))).strip()
            _validate_model_text(
                evidence_id=evidence_id,
                field_name="analysis",
                value=analysis,
                evidence_item=evidence_item,
                max_chars=1000,
                extra_number_context=pack_number_context,
            )
            analyses[evidence_id] = analysis

    missing_ids = sorted(allowed_ids - summaries.keys())
    if missing_ids:
        raise ValueError(f"Model omitted evidence IDs: {', '.join(missing_ids)}")
    return summaries, analyses


def parse_model_summaries(text: str, pack: EvidencePack) -> Dict[str, str]:
    payload = _extract_model_payload(text)
    if set(payload.keys()) != {"summaries"}:
        raise ValueError("Model output must contain only the summaries key.")
    summaries, _ = _parse_summary_entries(payload.get("summaries"), pack)
    return summaries


def parse_model_brief(
    text: str,
    pack: EvidencePack,
    holdings: Sequence[Mapping[str, Any]],
) -> ModelBrief:
    payload = _extract_model_payload(text)
    if set(payload.keys()) != {"summaries", "portfolio_actions"}:
        raise ValueError("Model output must contain only summaries and portfolio_actions.")

    summaries, analyses = _parse_summary_entries(
        payload.get("summaries"),
        pack,
        require_analysis=True,
    )
    action_entries = payload.get("portfolio_actions")
    if not isinstance(action_entries, list):
        raise ValueError("Model output must contain a portfolio_actions list.")

    expected_tickers = {
        str(holding.get("ticker", "")).upper().strip()
        for holding in holdings
        if str(holding.get("ticker", "")).strip()
    }
    allowed_ids = {item.id for item in pack.items}
    items_by_id = {item.id: item for item in pack.items}
    allowed_actions = {"加仓", "减仓", "持有", "观察"}
    allowed_confidence = {"低", "中", "高"}
    actions: List[PortfolioAction] = []
    seen_tickers: set[str] = set()

    for entry in action_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Every portfolio action entry must be an object.")
        expected_keys = {
            "ticker",
            "action",
            "confidence",
            "rationale",
            "evidence_ids",
            "watch_for",
        }
        if set(entry.keys()) != expected_keys:
            raise ValueError(
                "Every portfolio action must contain only ticker, action, confidence, "
                "rationale, evidence_ids, and watch_for."
            )

        ticker = str(entry.get("ticker", "")).upper().strip()
        action = str(entry.get("action", "")).strip()
        confidence = str(entry.get("confidence", "")).strip()
        rationale = _normalize_collection_gap_language(
            re.sub(r"\s+", " ", str(entry.get("rationale", ""))).strip()
        )
        watch_for = re.sub(r"\s+", " ", str(entry.get("watch_for", ""))).strip()
        raw_evidence_ids = entry.get("evidence_ids")
        if not isinstance(raw_evidence_ids, list):
            raise ValueError(f"Portfolio action for {ticker or '(empty)'} must contain evidence_ids.")
        evidence_ids = [str(value).strip() for value in raw_evidence_ids]

        if ticker not in expected_tickers:
            raise ValueError(f"Model returned an unknown holding ticker: {ticker or '(empty)'}")
        if ticker in seen_tickers:
            raise ValueError(f"Model returned duplicate portfolio action for {ticker}.")
        if action not in allowed_actions:
            raise ValueError(f"Model returned unsupported action for {ticker}: {action or '(empty)'}")
        if confidence not in allowed_confidence:
            raise ValueError(
                f"Model returned unsupported confidence for {ticker}: {confidence or '(empty)'}"
            )
        if not rationale or len(rationale) > 800:
            raise ValueError(f"Portfolio rationale for {ticker} must be 1-800 characters.")
        if not watch_for or len(watch_for) > 500:
            raise ValueError(f"Portfolio watch_for for {ticker} must be 1-500 characters.")
        if URL_RE.search(f"{rationale} {watch_for}"):
            raise ValueError(f"Portfolio action for {ticker} contains a URL.")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(f"Portfolio action for {ticker} contains duplicate evidence IDs.")

        unknown_ids = sorted(set(evidence_ids) - allowed_ids)
        if unknown_ids:
            raise ValueError(
                f"Portfolio action for {ticker} cites unknown evidence IDs: {', '.join(unknown_ids)}"
            )
        if action in {"加仓", "减仓"} and not evidence_ids:
            raise ValueError(f"Portfolio action {action} for {ticker} requires evidence IDs.")
        if not evidence_ids and confidence != "低":
            raise ValueError(f"Portfolio action for {ticker} without evidence must have low confidence.")

        evidence_text = " ".join(
            f"{items_by_id[evidence_id].title} {items_by_id[evidence_id].published_at} "
            f"{items_by_id[evidence_id].content}"
            for evidence_id in evidence_ids
        )
        allowed_numbers = _normalized_numbers(f"{' '.join(evidence_ids)} {evidence_text}")
        introduced_numbers = sorted(
            _normalized_numbers(f"{rationale} {watch_for}") - allowed_numbers
        )
        if introduced_numbers:
            raise ValueError(
                f"Portfolio action for {ticker} introduced unsupported numbers: "
                f"{', '.join(introduced_numbers)}"
            )

        seen_tickers.add(ticker)
        actions.append(
            PortfolioAction(
                ticker=ticker,
                action=action,
                confidence=confidence,
                rationale=rationale,
                evidence_ids=evidence_ids,
                watch_for=watch_for,
            )
        )

    missing_tickers = sorted(expected_tickers - seen_tickers)
    if missing_tickers:
        raise ValueError(f"Model omitted holding tickers: {', '.join(missing_tickers)}")
    return ModelBrief(summaries=summaries, analyses=analyses, portfolio_actions=actions)


def _item_matches_holdings(item: EvidenceItem, holding_tickers: set[str]) -> bool:
    return bool({ticker.upper() for ticker in item.matched_tickers} & holding_tickers)


def _display_datetime(value: str, timezone_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None or ZoneInfo is None:
        return value
    try:
        return parsed.astimezone(ZoneInfo(timezone_name)).isoformat(timespec="seconds")
    except Exception:
        return value


def _display_datetime_readable(value: str, timezone_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None or ZoneInfo is None:
        return value
    try:
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def _append_information_item(
    parts: List[str],
    item: EvidenceItem,
    summaries: Mapping[str, str],
    analyses: Mapping[str, str],
    *,
    heading_level: int,
    timezone_name: str,
) -> None:
    parts.extend([f"{'#' * heading_level} {item.id} · {item.title}", ""])
    summary = summaries.get(item.id)
    analysis = analyses.get(item.id)
    if summary:
        parts.append(f"- **摘要：** {summary}")
        if analysis:
            parts.append(f"- **解读：** {analysis}")
    else:
        parts.append(
            "- **解读：** 这条信息目前只采集到标题或提交元数据，适合先打开原文核验，"
            "不直接用于持仓操作判断。"
        )
    if item.matched_tickers:
        parts.append(f"- **相关代码：** {', '.join(item.matched_tickers)}")
    parts.extend(
        [
            f"- **来源链接：** [{item.source_name} 原文]({item.url})",
            f"- **时间：** {_display_datetime_readable(item.published_at, timezone_name)}",
            "",
        ]
    )


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _window_description(pack: EvidencePack) -> str:
    if pack.window_start and pack.window_end:
        return (
            f"{_display_datetime_readable(pack.window_start, pack.timezone)} 至 "
            f"{_display_datetime_readable(pack.window_end, pack.timezone)}"
        )
    return f"最近 {pack.lookback_hours} 小时"


def _is_a_share_item(item: EvidenceItem) -> bool:
    return item.source_name == "巨潮资讯网" or "A股" in item.matched_topics


def _append_information_group(
    parts: List[str],
    *,
    heading: str,
    items: Sequence[EvidenceItem],
    summaries: Mapping[str, str],
    analyses: Mapping[str, str],
    timezone_name: str,
    empty_message: str,
    compact_title_only: bool = False,
) -> None:
    parts.extend([f"### {heading}", ""])
    if not items:
        parts.extend([f"- {empty_message}", ""])
        return
    full_items = [item for item in items if not compact_title_only or item.evidence_level != "title_only"]
    title_only_items = [item for item in items if compact_title_only and item.evidence_level == "title_only"]
    for item in full_items:
        _append_information_item(
            parts,
            item,
            summaries,
            analyses,
            heading_level=4,
            timezone_name=timezone_name,
        )
    if title_only_items:
        grouped: Dict[str, List[EvidenceItem]] = {}
        for item in title_only_items:
            topic = next(
                (value for value in item.matched_topics if value and value != "A股"),
                "A股公告",
            )
            grouped.setdefault(topic, []).append(item)
        parts.extend(
            [
                "#### A股公告标题复核队列",
                "",
                "以下条目来自法定披露平台，但只有公告标题，未纳入持仓操作分析：",
                "",
            ]
        )
        for topic, topic_items in grouped.items():
            parts.extend([f"##### {topic}（{len(topic_items)} 条）", ""])
            for item in topic_items:
                parts.append(
                    f"- [{item.id} · {item.title}]({item.url}) · "
                    f"{_display_datetime_readable(item.published_at, timezone_name)} · "
                    "仅有标题，需打开原文核验具体影响。"
                )
            parts.append("")


def _coverage_status_label(status: str) -> str:
    return {
        "collected": "已采集",
        "no_items": "已查询，本窗口无条目",
        "disabled": "未启用",
        "reserved": "仅预留接口",
        "key_missing": "缺少密钥，未调用",
        "failed": "采集失败",
    }.get(status, status)


def model_summary_brief_markdown(
    pack: EvidencePack,
    summaries: Mapping[str, str],
    analyses: Mapping[str, str],
    run_date: date,
    *,
    holdings: Sequence[Mapping[str, Any]] = (),
    portfolio_actions: Sequence[PortfolioAction] = (),
) -> str:
    summary_items = [item for item in pack.items if item.evidence_level == "summary"]
    holding_tickers = {
        str(holding.get("ticker", "")).upper().strip()
        for holding in holdings
        if str(holding.get("ticker", "")).strip()
    }
    market_items = [item for item in pack.items if not _item_matches_holdings(item, holding_tickers)]
    a_share_market_items = [item for item in market_items if _is_a_share_item(item)]
    us_market_items = [item for item in market_items if not _is_a_share_item(item)]
    level_counts: Dict[str, int] = {}
    for item in pack.items:
        level_counts[item.evidence_level] = level_counts.get(item.evidence_level, 0) + 1

    parts = [
        f"# 每日研究简报 - {run_date.isoformat()}",
        "",
        f"- **信息窗口：** {_window_description(pack)}",
        "- **阅读说明：** 每条信息均附原始链接；完整采集覆盖和来源日志已单独保存。",
        "",
        "## 1. 市场总体资讯（可靠信源）",
        "",
    ]
    _append_information_group(
        parts,
        heading="A股市场整体与重点方向",
        items=a_share_market_items,
        summaries=summaries,
        analyses=analyses,
        timezone_name=pack.timezone,
        empty_message="本窗口内，已启用信源未采集到A股整体或重点方向条目；这不代表市场没有发生事件。",
        compact_title_only=True,
    )
    _append_information_group(
        parts,
        heading="美股市场整体与宏观驱动",
        items=us_market_items,
        summaries=summaries,
        analyses=analyses,
        timezone_name=pack.timezone,
        empty_message="本窗口内，已启用信源未采集到美股整体或宏观驱动条目；这不代表市场没有发生事件。",
    )

    parts.extend(["", "## 2. 持仓公司相关资讯（可靠信源）", ""])
    if not holdings:
        parts.append(
            "- 尚未配置美股持仓。请在 `config/sources.json` 的 "
            "`portfolios.us_equities.holdings` 中维护持仓。"
        )
    for holding in holdings:
        ticker = str(holding.get("ticker", "")).upper().strip()
        company = str(holding.get("company", ticker)).strip() or ticker
        related = [item for item in pack.items if ticker in {value.upper() for value in item.matched_tickers}]
        parts.extend([f"### {ticker} · {company}", ""])
        if not related:
            parts.append(
                "- 本窗口内，配置的可靠信源未采集到该持仓的公司相关条目；"
                "这不代表公司没有发生事件。"
            )
            parts.append("")
            continue
        for item in related:
            _append_information_item(
                parts,
                item,
                summaries,
                analyses,
                heading_level=4,
                timezone_name=pack.timezone,
            )

    actions_by_ticker = {action.ticker: action for action in portfolio_actions}
    items_by_id = {item.id: item for item in pack.items}
    parts.extend(
        [
            "",
            "## 3. 根据市场动态分析持仓应该作何操作",
            "",
            "> 以下为基于本期可靠来源证据的研究判断，不构成个性化投资建议。"
            "标题级和元数据级条目不参与操作判断；缺少充分证据时优先观察或维持原计划。",
            "",
            "| 持仓 | 操作 | 置信度 | 判断依据 | 证据 | 后续观察 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for holding in holdings:
        ticker = str(holding.get("ticker", "")).upper().strip()
        action = actions_by_ticker.get(ticker)
        if action is None:
            parts.append(
                f"| {_table_cell(ticker)} | 观察 | 低 | 本期未生成受校验的操作判断，"
                "暂不据此调整仓位。 | 无 | 等待可靠正文级证据。 |"
            )
            continue
        evidence_links = ", ".join(
            f"[{evidence_id}]({items_by_id[evidence_id].url})"
            for evidence_id in action.evidence_ids
            if evidence_id in items_by_id
        ) or "无"
        parts.append(
            f"| {_table_cell(ticker)} | {_table_cell(action.action)} | "
            f"{_table_cell(action.confidence)} | {_table_cell(action.rationale)} | "
            f"{evidence_links} | {_table_cell(action.watch_for)} |"
        )

    return "\n".join(parts).strip() + "\n"


def _source_type_label(source_type: str) -> str:
    return {
        "primary": "官方/法定来源",
        "reputable_reporting": "可靠报道",
        "market_data_aggregator": "行情数据",
        "news_aggregator": "新闻聚合",
    }.get(source_type, source_type)


def _evidence_level_label(level: str) -> str:
    return {
        "summary": "可摘要",
        "metadata_only": "仅元数据",
        "title_only": "仅标题",
    }.get(level, level)


def source_log_markdown(pack: EvidencePack) -> str:
    level_counts: Dict[str, int] = {}
    for item in pack.items:
        level_counts[item.evidence_level] = level_counts.get(item.evidence_level, 0) + 1

    parts = [
        "# 简报采集日志",
        "",
        f"- **信息窗口：** {_window_description(pack)}",
        f"- **证据总数：** {len(pack.items)} 条",
        (
            "- **证据类型：** "
            f"可摘要 {level_counts.get('summary', 0)}；"
            f"仅元数据 {level_counts.get('metadata_only', 0)}；"
            f"仅标题 {level_counts.get('title_only', 0)}。"
        ),
        "- **说明：** 仅标题和仅元数据条目只用于展示与复核，不用于持仓操作分析。",
    ]
    if pack.errors:
        parts.append("- **采集告警：** " + "；".join(pack.errors))

    if pack.coverage:
        parts.extend(["", "## 采集覆盖", ""])
        for entry in pack.coverage:
            detail = f"；{entry.detail}" if entry.detail else ""
            parts.append(
                f"- **{entry.name}**：{_coverage_status_label(entry.status)}，"
                f"{entry.item_count} 条{detail}"
            )

    parts.extend(["", "## 来源明细", ""])
    for item in pack.items:
        tickers = f"；相关代码：{', '.join(item.matched_tickers)}" if item.matched_tickers else ""
        parts.append(
            f"- [{item.id} · {item.title}]({item.url}) · "
            f"{_source_type_label(item.source_type)} · {_evidence_level_label(item.evidence_level)} · "
            f"{_display_datetime_readable(item.published_at, pack.timezone)}{tickers}"
        )
    return "\n".join(parts).strip() + "\n"


def evidence_only_brief_markdown(
    pack: EvidencePack,
    run_date: date,
    *,
    holdings: Sequence[Mapping[str, Any]] = (),
) -> str:
    actions = [
        PortfolioAction(
            ticker=str(holding.get("ticker", "")).upper().strip(),
            action="观察",
            confidence="低",
            rationale="本窗口没有可供模型分析的正文级证据，暂不据此调整仓位。",
            evidence_ids=[],
            watch_for="先核验相关标题或元数据，并等待可靠正文级证据。",
        )
        for holding in holdings
        if str(holding.get("ticker", "")).strip()
    ]
    return model_summary_brief_markdown(
        pack,
        {},
        {},
        run_date,
        holdings=holdings,
        portfolio_actions=actions,
    )


def validate_summary_citations(text: str, pack: EvidencePack) -> List[str]:
    allowed = {canonical_url(item.url) for item in pack.items}
    used = {canonical_url(url.rstrip(".,;:`'\"")) for url in URL_RE.findall(text)}
    errors = [f"Unverified citation URL: {url}" for url in sorted(used - allowed)]
    if pack.items and not used:
        errors.append("The generated brief contains no evidence source URLs.")
    return errors


def evidence_pack_json(pack: EvidencePack) -> str:
    return json.dumps(pack.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
