from __future__ import annotations

import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

from .evidence import (
    EvidenceItem,
    EvidencePack,
    SourceCoverage,
    configured_a_share_holdings,
    configured_holdings,
    dedupe_evidence,
    new_evidence_pack,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


class CollectionError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        value = data.strip()
        if value:
            self.parts.append(value)


def strip_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(value or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def parse_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def hostname_allowed(url: str, allowed_domains: Sequence[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return any(hostname == domain.lower() or hostname.endswith(f".{domain.lower()}") for domain in allowed_domains)


def _local_timezone(name: str):
    if ZoneInfo is None:
        return timezone.utc
    return ZoneInfo(name)


def resolve_research_window(
    settings: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)

    mode = str(settings.get("freshness_window", "previous_day_to_run"))
    if mode == "previous_day_to_run":
        local_tz = _local_timezone(str(settings.get("timezone", "Asia/Shanghai")))
        local_end = end.astimezone(local_tz)
        local_start = datetime.combine(
            local_end.date() - timedelta(days=1),
            time.min,
            tzinfo=local_tz,
        )
        start = local_start.astimezone(timezone.utc)
    else:
        start = end - timedelta(hours=int(settings.get("lookback_hours", 24)))
        mode = "rolling_hours"
    return start, end, mode


def window_duration_hours(start: datetime, end: datetime) -> int:
    return max(1, math.ceil((end - start).total_seconds() / 3600))


class HttpClient:
    def __init__(self, user_agent: str, timeout: int = 30) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def request_bytes(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise CollectionError(f"HTTP {exc.code} for {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CollectionError(f"Could not fetch {url}: {exc.reason}") from exc

    def get_json(self, url: str) -> Any:
        return json.loads(self.request_bytes(url).decode("utf-8"))

    def get_text(self, url: str) -> str:
        return self.request_bytes(url).decode("utf-8", errors="replace")

    def post_form_json(self, url: str, values: Mapping[str, str], headers: Mapping[str, str]) -> Any:
        body = urllib.parse.urlencode(values).encode("utf-8")
        merged_headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        merged_headers.update(headers)
        return json.loads(self.request_bytes(url, method="POST", body=body, headers=merged_headers).decode("utf-8"))


def _first_text(element: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
        for child in element:
            if child.tag.split("}")[-1] == name.split(":")[-1] and child.text:
                return child.text.strip()
    return ""


def _entry_link(element: ET.Element) -> str:
    direct = _first_text(element, ["link"])
    if direct:
        return direct
    for child in element:
        if child.tag.split("}")[-1] == "link" and child.attrib.get("href"):
            return child.attrib["href"].strip()
    return ""


def collect_rss_items(
    *,
    client: HttpClient,
    feed: Mapping[str, Any],
    cutoff: datetime,
    window_end: datetime | None = None,
) -> List[EvidenceItem]:
    name = str(feed["name"])
    url = str(feed["url"])
    allowed_domains = list(feed.get("allowed_domains", []))
    source_type = str(feed.get("source_type", "primary"))
    limit = int(feed.get("limit", 10))
    root = ET.fromstring(client.request_bytes(url))
    elements = [
        element
        for element in root.iter()
        if element.tag.split("}")[-1] in {"item", "entry"}
    ]
    items: List[EvidenceItem] = []
    for element in elements:
        title = strip_html(_first_text(element, ["title"]))
        link = _entry_link(element)
        published = parse_datetime(_first_text(element, ["pubDate", "published", "updated", "dc:date"]))
        summary = strip_html(_first_text(element, ["description", "summary", "content"]))
        if (
            not title
            or not link
            or not published
            or published < cutoff
            or (window_end is not None and published > window_end)
        ):
            continue
        if allowed_domains and not hostname_allowed(link, allowed_domains):
            continue
        evidence_level = "summary" if summary and summary.casefold() != title.casefold() else "title_only"
        items.append(
            EvidenceItem(
                id="",
                title=title,
                published_at=published.isoformat(timespec="seconds"),
                source_name=name,
                source_type=source_type,
                url=link,
                content=summary
                or f"Official feed item title: {title}. Open the original source to verify details.",
                matched_topics=list(feed.get("topics", [])),
                matched_tickers=[str(ticker).upper() for ticker in feed.get("tickers", [])],
                evidence_level=evidence_level,
            )
        )
        if len(items) >= limit:
            break
    return items


def _ticker_cik_map(payload: Mapping[str, Any]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for record in payload.values():
        if not isinstance(record, Mapping):
            continue
        ticker = str(record.get("ticker", "")).upper().strip()
        cik = str(record.get("cik_str", "")).strip()
        if ticker and cik:
            mapping[ticker] = cik.zfill(10)
    return mapping


def _sequence_value(values: Mapping[str, Any], key: str, index: int) -> str:
    raw = values.get(key, [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or index >= len(raw):
        return ""
    return str(raw[index])


def collect_sec_filings(
    *,
    client: HttpClient,
    sources: Mapping[str, Any],
    config: Mapping[str, Any],
    cutoff: datetime,
    start_date: date | None = None,
    end_date: date | None = None,
    window_end: datetime | None = None,
) -> List[EvidenceItem]:
    ticker_map = _ticker_cik_map(client.get_json("https://www.sec.gov/files/company_tickers.json"))
    forms = {str(form).upper() for form in config.get("forms", ["8-K", "10-Q", "10-K", "6-K", "20-F"])}
    max_per_company = int(config.get("max_per_company", 5))
    fetch_document_text = bool(config.get("fetch_document_text", True))
    max_document_chars = int(config.get("max_document_chars", 12000))
    min_document_chars = int(config.get("min_document_chars", 200))
    watchlist = configured_holdings(sources)
    items: List[EvidenceItem] = []
    for company in watchlist:
        ticker = str(company.get("ticker", "")).upper().strip()
        cik = str(company.get("cik", "")).zfill(10) if company.get("cik") else ticker_map.get(ticker, "")
        if not ticker or not cik:
            continue
        payload = client.get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = payload.get("filings", {}).get("recent", {})
        count = 0
        for index, form in enumerate(recent.get("form", [])):
            form = str(form).upper()
            if form not in forms:
                continue
            filed = _sequence_value(recent, "filingDate", index)
            try:
                filed_date = date.fromisoformat(filed)
            except ValueError:
                continue
            if filed_date < (start_date or cutoff.date()):
                continue
            if end_date is not None and filed_date > end_date:
                continue
            accepted = parse_datetime(_sequence_value(recent, "acceptanceDateTime", index))
            if accepted and (accepted < cutoff or (window_end is not None and accepted > window_end)):
                continue
            accession = _sequence_value(recent, "accessionNumber", index)
            document = _sequence_value(recent, "primaryDocument", index)
            description = _sequence_value(recent, "primaryDocDescription", index)
            report_date = _sequence_value(recent, "reportDate", index)
            accession_path = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{document}"
            title = f"{ticker} filed {form}: {description or document}"
            metadata = (
                f"SEC filing by {payload.get('name', ticker)}. Form: {form}. "
                f"Filed: {filed}. Report date: {report_date or '(not provided)'}. "
                f"Primary document description: {description or document}."
            )
            document_text = ""
            if fetch_document_text and document:
                try:
                    document_text = strip_html(client.get_text(url))[:max_document_chars]
                except Exception:
                    document_text = ""
            evidence_level = "summary" if len(document_text) >= min_document_chars else "metadata_only"
            content = metadata
            if document_text:
                content += f" Filing text excerpt: {document_text}"
            items.append(
                EvidenceItem(
                    id="",
                    title=title,
                    published_at=(accepted or datetime.fromisoformat(f"{filed}T00:00:00+00:00")).isoformat(
                        timespec="seconds"
                    ),
                    source_name="SEC EDGAR",
                    source_type="primary",
                    url=url,
                    content=content,
                    matched_topics=list(company.get("themes", [])),
                    matched_tickers=[ticker],
                    evidence_level=evidence_level,
                )
            )
            count += 1
            if count >= max_per_company:
                break
    return items


def collect_cninfo_announcements(
    *,
    client: HttpClient,
    config: Mapping[str, Any],
    cutoff: datetime,
    window_end: datetime | None = None,
    holdings: Sequence[Mapping[str, Any]] = (),
) -> List[EvidenceItem]:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    market_queries = [str(value) for value in config.get("market_queries", [])]
    theme_queries = [
        str(value) for value in config.get("theme_queries", config.get("queries", []))
    ]
    query_specs = [
        {"searchkey": query, "stock": "", "topic": f"A股市场事件：{query}"}
        for query in market_queries
    ]
    query_specs.extend(
        {"searchkey": query, "stock": "", "topic": f"A股重点方向：{query}"}
        for query in theme_queries
    )
    for holding in holdings:
        ticker = str(holding.get("ticker", "")).strip()
        company = str(holding.get("company", "")).strip()
        if ticker:
            query_specs.append(
                {
                    "searchkey": "",
                    "stock": f"{ticker},{company}" if company else ticker,
                    "topic": f"A股持仓：{ticker}",
                }
            )
    page_size = int(config.get("page_size", config.get("max_per_query", 5)))
    max_pages = int(config.get("max_pages", 1))
    start = cutoff.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    effective_end = window_end or datetime.now(timezone.utc)
    end = effective_end.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    items: List[EvidenceItem] = []
    for query_spec in query_specs:
        searchkey = str(query_spec["searchkey"])
        stock = str(query_spec["stock"])
        for page_num in range(1, max_pages + 1):
            payload = client.post_form_json(
                endpoint,
                {
                    "pageNum": str(page_num),
                    "pageSize": str(page_size),
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": stock,
                    "searchkey": searchkey,
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": f"{start}~{end}",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
                {
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://www.cninfo.com.cn",
                    "Referer": (
                        "https://www.cninfo.com.cn/new/fulltextSearch?keyWord="
                        f"{urllib.parse.quote(searchkey)}"
                    ),
                },
            )
            announcements = payload.get("announcements") or []
            for announcement in announcements:
                timestamp = int(announcement.get("announcementTime", 0)) / 1000
                published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                if published < cutoff or published > effective_end:
                    continue
                title = strip_html(str(announcement.get("announcementTitle", "")))
                adjunct_url = str(announcement.get("adjunctUrl", "")).strip()
                url = urljoin("https://static.cninfo.com.cn/", adjunct_url) if adjunct_url else ""
                ticker = str(announcement.get("secCode", "")).strip()
                company = strip_html(str(announcement.get("secName", "")))
                if not title or not url:
                    continue
                items.append(
                    EvidenceItem(
                        id="",
                        title=f"{ticker} {company}: {title}".strip(),
                        published_at=published.isoformat(timespec="seconds"),
                        source_name="巨潮资讯网",
                        source_type="primary",
                        url=url,
                        content=(
                            f"巨潮资讯网法定披露公告。证券代码：{ticker or '(none)'}；"
                            f"证券简称：{company or '(none)'}；公告标题：{title}。"
                            "仅可依据公告标题判断，需打开原始 PDF 核验详细内容。"
                        ),
                        matched_topics=["A股", str(query_spec["topic"])],
                        matched_tickers=[ticker] if ticker else [],
                        evidence_level="title_only",
                    )
                )
            if len(announcements) < page_size or payload.get("hasMore") is False:
                break
    return items


def collect_yahoo_market_snapshot(
    *,
    client: HttpClient,
    instrument: Mapping[str, Any],
    cutoff: datetime,
    window_end: datetime,
) -> List[EvidenceItem]:
    symbol = str(instrument.get("symbol", "")).strip()
    name = str(instrument.get("name", symbol)).strip() or symbol
    if not symbol:
        return []

    query_start = int((cutoff - timedelta(days=7)).timestamp())
    query_end = int((window_end + timedelta(days=1)).timestamp())
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    api_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?"
        + urllib.parse.urlencode(
            {
                "period1": str(query_start),
                "period2": str(query_end),
                "interval": "1d",
                "events": "history",
            }
        )
    )
    payload = client.get_json(api_url)
    results = payload.get("chart", {}).get("result") or []
    if not results:
        error = payload.get("chart", {}).get("error")
        raise CollectionError(f"Yahoo Finance returned no data for {symbol}: {error}")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_rows = result.get("indicators", {}).get("quote") or []
    quotes = quote_rows[0] if quote_rows else {}
    closes = quotes.get("close") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    available: List[tuple[int, datetime, float]] = []
    for index, raw_timestamp in enumerate(timestamps):
        if index >= len(closes) or closes[index] is None:
            continue
        published = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
        available.append((index, published, float(closes[index])))

    in_window = [row for row in available if cutoff <= row[1] <= window_end]
    if not in_window:
        return []
    index, published, close = in_window[-1]
    previous_rows = [row for row in available if row[1] < published]
    previous_close = previous_rows[-1][2] if previous_rows else None
    change = close - previous_close if previous_close not in {None, 0} else None
    change_pct = (change / previous_close * 100) if change is not None and previous_close else None
    direction = "持平"
    if change is not None and change > 0:
        direction = "上涨"
    elif change is not None and change < 0:
        direction = "下跌"

    def value_at(values: Sequence[Any], position: int) -> str:
        if position >= len(values) or values[position] is None:
            return "(not provided)"
        return f"{float(values[position]):.4f}"

    currency = str(result.get("meta", {}).get("currency", "")).strip()
    movement = (
        f"{direction} {abs(change):.4f}（{change_pct:+.2f}%）"
        if change is not None and change_pct is not None
        else "缺少上一交易日可比收盘值"
    )
    content = (
        f"Yahoo Finance daily market snapshot for {name} ({symbol}). "
        f"Close: {close:.4f} {currency}. Daily movement versus the prior available close: "
        f"{movement}. High: {value_at(highs, index)}. Low: {value_at(lows, index)}."
    )
    title_movement = f"{direction} {abs(change_pct):.2f}%" if change_pct is not None else "最新日线快照"
    return [
        EvidenceItem(
            id="",
            title=f"{name}：{title_movement}",
            published_at=published.isoformat(timespec="seconds"),
            source_name="Yahoo Finance",
            source_type=str(instrument.get("source_type", "market_data_aggregator")),
            url=f"https://finance.yahoo.com/quote/{encoded_symbol}",
            content=content,
            matched_topics=[str(value) for value in instrument.get("topics", [])],
            matched_tickers=[],
            evidence_level="summary",
        )
    ]


def collect_finnhub_news(
    *,
    client: HttpClient,
    sources: Mapping[str, Any],
    config: Mapping[str, Any],
    token: str,
    cutoff: datetime,
    window_end: datetime,
) -> List[EvidenceItem]:
    allowed_publishers = {
        str(value).casefold().strip()
        for value in config.get("allowed_publishers", [])
        if str(value).strip()
    }
    max_per_request = int(config.get("max_per_request", 20))
    source_type = str(config.get("source_type", "reputable_reporting"))
    requests_to_make: List[tuple[str, str]] = []
    if config.get("include_market_news", True):
        query = urllib.parse.urlencode(
            {"category": str(config.get("market_category", "general")), "token": token}
        )
        requests_to_make.append((f"https://finnhub.io/api/v1/news?{query}", ""))
    if config.get("include_company_news", True):
        for holding in configured_holdings(sources):
            ticker = str(holding["ticker"])
            query = urllib.parse.urlencode(
                {
                    "symbol": ticker,
                    "from": cutoff.date().isoformat(),
                    "to": window_end.date().isoformat(),
                    "token": token,
                }
            )
            requests_to_make.append((f"https://finnhub.io/api/v1/company-news?{query}", ticker))

    items: List[EvidenceItem] = []
    for url, ticker in requests_to_make:
        payload = client.get_json(url)
        if not isinstance(payload, list):
            continue
        request_count = 0
        for article in payload:
            if not isinstance(article, Mapping):
                continue
            publisher = str(article.get("source", "")).strip()
            if allowed_publishers and publisher.casefold() not in allowed_publishers:
                continue
            raw_timestamp = article.get("datetime")
            try:
                published = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                continue
            if published < cutoff or published > window_end:
                continue
            title = strip_html(str(article.get("headline", "")))
            article_url = str(article.get("url", "")).strip()
            summary = strip_html(str(article.get("summary", "")))
            if not title or not article_url:
                continue
            related = ticker or str(article.get("related", "")).upper().strip()
            items.append(
                EvidenceItem(
                    id="",
                    title=title,
                    published_at=published.isoformat(timespec="seconds"),
                    source_name=f"Finnhub / {publisher or 'publisher not provided'}",
                    source_type=source_type,
                    url=article_url,
                    content=summary or f"Finnhub news item title: {title}. Open the source to verify details.",
                    matched_topics=["美股市场新闻"] if not ticker else ["美股持仓新闻"],
                    matched_tickers=[related] if related else [],
                    evidence_level="summary" if summary else "title_only",
                )
            )
            request_count += 1
            if request_count >= max_per_request:
                break
    return items


def _record_coverage(
    pack: EvidencePack,
    *,
    name: str,
    category: str,
    status: str,
    item_count: int = 0,
    detail: str = "",
) -> None:
    pack.coverage.append(
        SourceCoverage(
            name=name,
            category=category,
            status=status,
            item_count=item_count,
            detail=detail,
        )
    )


def collect_evidence(
    *,
    settings: Mapping[str, Any],
    sources: Mapping[str, Any],
    client: HttpClient | None = None,
    now: datetime | None = None,
) -> EvidencePack:
    collector_settings = settings.get("collectors", {})
    source_collectors = sources.get("collectors", {})
    user_agent = os.environ.get(
        "RESEARCH_USER_AGENT",
        str(collector_settings.get("user_agent", "dailyresearch/0.1 research@example.com")),
    )
    http_client = client or HttpClient(
        user_agent=user_agent,
        timeout=int(collector_settings.get("timeout_seconds", 30)),
    )
    cutoff, window_end, window_mode = resolve_research_window(settings, now=now)
    local_tz = _local_timezone(str(settings.get("timezone", "Asia/Shanghai")))
    pack = new_evidence_pack(
        window_duration_hours(cutoff, window_end),
        window_start=cutoff.astimezone(local_tz),
        window_end=window_end.astimezone(local_tz),
        window_mode=window_mode,
        timezone_name=str(settings.get("timezone", "Asia/Shanghai")),
    )
    collected: List[EvidenceItem] = []

    yahoo_config = source_collectors.get("yahoo_market_snapshots", {})
    if yahoo_config.get("enabled", False):
        for instrument in yahoo_config.get("instruments", []):
            name = f"Yahoo Finance / {instrument.get('name', instrument.get('symbol', 'unknown'))}"
            try:
                items = collect_yahoo_market_snapshot(
                    client=http_client,
                    instrument=instrument,
                    cutoff=cutoff,
                    window_end=window_end,
                )
                collected.extend(items)
                _record_coverage(
                    pack,
                    name=name,
                    category="market_snapshot",
                    status="collected" if items else "no_items",
                    item_count=len(items),
                )
            except Exception as exc:
                pack.errors.append(f"Yahoo Finance collector failed for {name}: {exc}")
                _record_coverage(
                    pack,
                    name=name,
                    category="market_snapshot",
                    status="failed",
                    detail=str(exc),
                )
    else:
        _record_coverage(
            pack,
            name="Yahoo Finance Market Snapshots",
            category="market_snapshot",
            status="disabled",
            detail=str(yahoo_config.get("disabled_reason", "Disabled by configuration.")),
        )

    sec_config = source_collectors.get("sec_filings", {})
    if sec_config.get("enabled", True):
        try:
            items = collect_sec_filings(
                client=http_client,
                sources=sources,
                config=sec_config,
                cutoff=cutoff,
                start_date=cutoff.astimezone(local_tz).date(),
                end_date=window_end.astimezone(local_tz).date(),
                window_end=window_end,
            )
            collected.extend(items)
            _record_coverage(
                pack,
                name="SEC EDGAR",
                category="official_filing",
                status="collected" if items else "no_items",
                item_count=len(items),
                detail="逐一查询已配置的美股持仓。",
            )
        except Exception as exc:
            pack.errors.append(f"SEC EDGAR collector failed: {exc}")
            _record_coverage(
                pack,
                name="SEC EDGAR",
                category="official_filing",
                status="failed",
                detail=str(exc),
            )
    else:
        _record_coverage(
            pack,
            name="SEC EDGAR",
            category="official_filing",
            status="disabled",
            detail=str(sec_config.get("disabled_reason", "Disabled by configuration.")),
        )

    for feed in source_collectors.get("rss_feeds", []):
        feed_name = str(feed.get("name", feed.get("url", "unknown RSS")))
        if not feed.get("enabled", True):
            _record_coverage(
                pack,
                name=feed_name,
                category="official_rss",
                status="disabled",
                detail=str(feed.get("disabled_reason", "Disabled by configuration.")),
            )
            continue
        try:
            items = collect_rss_items(
                client=http_client,
                feed=feed,
                cutoff=cutoff,
                window_end=window_end,
            )
            collected.extend(items)
            _record_coverage(
                pack,
                name=feed_name,
                category="official_rss",
                status="collected" if items else "no_items",
                item_count=len(items),
            )
        except Exception as exc:
            pack.errors.append(f"RSS collector failed for {feed.get('name', feed.get('url'))}: {exc}")
            _record_coverage(
                pack,
                name=feed_name,
                category="official_rss",
                status="failed",
                detail=str(exc),
            )

    cninfo_config = source_collectors.get("cninfo", {})
    if cninfo_config.get("enabled", True):
        try:
            items = collect_cninfo_announcements(
                client=http_client,
                config=cninfo_config,
                cutoff=cutoff,
                window_end=window_end,
                holdings=configured_a_share_holdings(sources),
            )
            collected.extend(items)
            _record_coverage(
                pack,
                name="巨潮资讯网",
                category="official_filing",
                status="collected" if items else "no_items",
                item_count=len(items),
                detail="查询A股市场事件、重点方向及已配置的A股持仓。",
            )
        except Exception as exc:
            pack.errors.append(f"CNINFO collector failed: {exc}")
            _record_coverage(
                pack,
                name="巨潮资讯网",
                category="official_filing",
                status="failed",
                detail=str(exc),
            )
    else:
        _record_coverage(
            pack,
            name="巨潮资讯网",
            category="official_filing",
            status="disabled",
            detail=str(cninfo_config.get("disabled_reason", "Disabled by configuration.")),
        )

    finnhub_config = source_collectors.get("finnhub", {})
    if finnhub_config.get("enabled", False):
        token = os.environ.get("FINNHUB_API_KEY", "").strip()
        if not token:
            _record_coverage(
                pack,
                name="Finnhub News",
                category="news_aggregator",
                status="key_missing",
                detail="配置 FINNHUB_API_KEY 后才会调用已实现的适配器。",
            )
        else:
            try:
                items = collect_finnhub_news(
                    client=http_client,
                    sources=sources,
                    config=finnhub_config,
                    token=token,
                    cutoff=cutoff,
                    window_end=window_end,
                )
                collected.extend(items)
                _record_coverage(
                    pack,
                    name="Finnhub News",
                    category="news_aggregator",
                    status="collected" if items else "no_items",
                    item_count=len(items),
                )
            except Exception as exc:
                pack.errors.append(f"Finnhub collector failed: {exc}")
                _record_coverage(
                    pack,
                    name="Finnhub News",
                    category="news_aggregator",
                    status="failed",
                    detail=str(exc),
                )
    else:
        _record_coverage(
            pack,
            name="Finnhub News",
            category="news_aggregator",
            status="disabled",
            detail=str(finnhub_config.get("disabled_reason", "需要显式启用并配置 API 密钥。")),
        )

    for source in source_collectors.get("reserved_sources", []):
        _record_coverage(
            pack,
            name=str(source.get("name", "Reserved source")),
            category=str(source.get("category", "reserved")),
            status=str(source.get("status", "reserved")),
            detail=str(source.get("reason", "")),
        )

    pack.items = dedupe_evidence(
        collected,
        max_items=int(collector_settings.get("max_evidence_items", 60)),
    )
    return pack
