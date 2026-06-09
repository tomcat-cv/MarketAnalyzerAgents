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
    configured_focus_topics,
    configured_holdings,
    configured_portfolio_holdings,
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


def _xml_child_text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    if found is not None and found.text:
        return found.text.strip()
    return ""


def _extract_sec_form4_details(raw_document: str) -> dict[str, str]:
    try:
        root = ET.fromstring(raw_document.encode("utf-8"))
    except ET.ParseError:
        return {}

    owner_name = _xml_child_text(root, ".//reportingOwnerId/rptOwnerName")
    issuer_name = _xml_child_text(root, ".//issuer/issuerName")
    issuer_ticker = _xml_child_text(root, ".//issuer/issuerTradingSymbol")
    relationship_parts = []
    relationship = root.find(".//reportingOwnerRelationship")
    if _xml_child_text(relationship, "isDirector") in {"1", "true", "True"}:
        relationship_parts.append("董事")
    if _xml_child_text(relationship, "isOfficer") in {"1", "true", "True"}:
        officer_title = _xml_child_text(relationship, "officerTitle")
        relationship_parts.append(f"高管{f'（{officer_title}）' if officer_title else ''}")
    if _xml_child_text(relationship, "isTenPercentOwner") in {"1", "true", "True"}:
        relationship_parts.append("10%以上股东")
    if _xml_child_text(relationship, "isOther") in {"1", "true", "True"}:
        other_text = _xml_child_text(relationship, "otherText")
        relationship_parts.append(other_text or "其他关系人")

    acquisition_labels = {
        "A": "取得/买入或获授",
        "D": "处置/卖出或转出",
    }
    transaction_lines: List[str] = []
    sale_shares_total = 0.0
    gift_shares_total = 0.0
    sale_value_total = 0.0
    first_pre_transaction_owned = ""
    for transaction in root.findall(".//nonDerivativeTransaction"):
        security = _xml_child_text(transaction, "securityTitle/value")
        date_value = _xml_child_text(transaction, "transactionDate/value")
        code = _xml_child_text(transaction, "transactionCoding/transactionCode")
        shares = _xml_child_text(transaction, "transactionAmounts/transactionShares/value")
        price = _xml_child_text(transaction, "transactionAmounts/transactionPricePerShare/value")
        acquired_disposed = _xml_child_text(
            transaction,
            "transactionAmounts/transactionAcquiredDisposedCode/value",
        )
        owned_after = _xml_child_text(
            transaction,
            "postTransactionAmounts/sharesOwnedFollowingTransaction/value",
        )
        direct = _xml_child_text(transaction, "ownershipNature/directOrIndirectOwnership/value")
        direction = acquisition_labels.get(acquired_disposed, acquired_disposed or "未标明方向")
        pre_transaction_owned = ""
        try:
            shares_value = float(shares)
            price_value = float(price) if price else 0.0
            owned_after_value = float(owned_after)
            if acquired_disposed == "D":
                pre_transaction_owned = f"{owned_after_value + shares_value:.0f}"
            elif acquired_disposed == "A":
                pre_transaction_owned = f"{owned_after_value - shares_value:.0f}"
            if not first_pre_transaction_owned and pre_transaction_owned:
                first_pre_transaction_owned = pre_transaction_owned
            if code == "S" and acquired_disposed == "D":
                sale_shares_total += shares_value
                sale_value_total += shares_value * price_value
            if code == "G" and acquired_disposed == "D":
                gift_shares_total += shares_value
        except (TypeError, ValueError):
            pre_transaction_owned = ""
        transaction_lines.append(
            "；".join(
                value
                for value in [
                    f"证券：{security}" if security else "",
                    f"日期：{date_value}" if date_value else "",
                    f"交易代码：{code}" if code else "",
                    f"方向：{direction}",
                    f"股数：{shares}" if shares else "",
                    f"每股价格：{price}" if price else "",
                    f"交易前估算持股：{pre_transaction_owned}" if pre_transaction_owned else "",
                    f"交易后持股：{owned_after}" if owned_after else "",
                    f"持有性质：{direct}" if direct else "",
                ]
                if value
            )
        )

    lines = [
        f"发行人：{issuer_name}" if issuer_name else "",
        f"证券代码：{issuer_ticker}" if issuer_ticker else "",
        f"报告人：{owner_name}" if owner_name else "",
        f"报告人与公司关系：{', '.join(relationship_parts)}" if relationship_parts else "",
    ]
    if transaction_lines:
        lines.append("非衍生证券交易明细：" + " | ".join(transaction_lines))
    aggregate_parts = []
    if first_pre_transaction_owned:
        aggregate_parts.append(f"首笔交易前估算持股：{first_pre_transaction_owned}")
    if sale_shares_total:
        aggregate_parts.append(f"卖出合计股数：{sale_shares_total:.0f}")
        aggregate_parts.append(
            f"卖出交易估算总金额：{sale_value_total:.2f}美元"
        )
        aggregate_parts.append(f"卖出交易估算总金额约{sale_value_total / 100000000:.2f}亿美元")
        aggregate_parts.append(f"卖出交易估算总金额约{sale_value_total / 100000000:.1f}亿美元")
    if gift_shares_total:
        aggregate_parts.append(f"赠与或无对价转出合计股数：{gift_shares_total:.0f}")
    if aggregate_parts:
        lines.append("汇总：" + "；".join(aggregate_parts))
    if not any(lines):
        return {}
    return {
        "owner_name": owner_name,
        "description": " ".join(line for line in lines if line),
    }


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
            raw_document = ""
            structured_details = {}
            if fetch_document_text and document:
                try:
                    raw_document = client.get_text(url)
                    if form == "4":
                        structured_details = _extract_sec_form4_details(raw_document)
                        if not structured_details and "/xslF345X" in url and document.endswith(".xml"):
                            raw_xml_url = re.sub(r"/xslF345X\d+/", "/", url)
                            structured_details = _extract_sec_form4_details(client.get_text(raw_xml_url))
                    document_text = strip_html(raw_document)[:max_document_chars]
                except Exception:
                    document_text = ""
            if form == "4" and structured_details.get("owner_name"):
                title = f"{ticker} 内部人持股变动：{structured_details['owner_name']}"
            evidence_level = "summary" if len(document_text) >= min_document_chars else "metadata_only"
            content = metadata
            if structured_details.get("description"):
                content += f" Structured Form 4 details: {structured_details['description']}."
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
    interval = str(instrument.get("interval", "1d")).strip() or "1d"
    api_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?"
        + urllib.parse.urlencode(
            {
                "period1": str(query_start),
                "period2": str(query_end),
                "interval": interval,
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
    if len(in_window) > 1:
        baseline_index, baseline_time, baseline_close = in_window[0]
        baseline_label = "窗口首个可比价格"
        title_movement_prefix = "窗口"
    else:
        previous_rows = [row for row in available if row[1] < published]
        baseline_index, baseline_time, baseline_close = (
            previous_rows[-1] if previous_rows else (index, published, None)
        )
        baseline_label = "窗口前一可比价格"
        title_movement_prefix = "较前值"
    change = close - baseline_close if baseline_close not in {None, 0} else None
    change_pct = (change / baseline_close * 100) if change is not None and baseline_close else None
    direction = "持平"
    if change is not None and change > 0:
        direction = "上涨"
    elif change is not None and change < 0:
        direction = "下跌"

    def value_at(values: Sequence[Any], position: int) -> str:
        if position >= len(values) or values[position] is None:
            return "(not provided)"
        return f"{float(values[position]):.4f}"

    def numeric_values(values: Sequence[Any], positions: Sequence[int]) -> List[float]:
        result_values: List[float] = []
        for position in positions:
            if position < len(values) and values[position] is not None:
                result_values.append(float(values[position]))
        return result_values

    currency = str(result.get("meta", {}).get("currency", "")).strip()
    movement = (
        f"{direction} {abs(change):.4f}（{change_pct:+.2f}%）"
        if change is not None and change_pct is not None
        else "缺少上一交易日可比收盘值"
    )
    in_window_positions = [row[0] for row in in_window]
    window_highs = numeric_values(highs, in_window_positions)
    window_lows = numeric_values(lows, in_window_positions)
    high = f"{max(window_highs):.4f}" if window_highs else value_at(highs, index)
    low = f"{min(window_lows):.4f}" if window_lows else value_at(lows, index)
    window_range_text = ""
    if window_highs and window_lows and min(window_lows) != 0:
        window_range = max(window_highs) - min(window_lows)
        window_range_pct = window_range / min(window_lows) * 100
        window_range_text = (
            f" Window range: {window_range:.4f}. "
            f"Rounded window range: {round(window_range):d}. "
            f"Window range percent versus window low: {window_range_pct:.2f}%."
        )
    currency_suffix = f" {currency}" if currency else ""
    content = (
        f"Yahoo Finance price snapshot for {name} ({symbol}). "
        f"Query interval: {interval}. Collection window: {cutoff.isoformat(timespec='seconds')} "
        f"to {window_end.isoformat(timespec='seconds')}. "
        f"Latest available price: {close:.4f}{currency_suffix} at "
        f"{published.isoformat(timespec='seconds')}. "
        f"Movement versus {baseline_label}"
    )
    if baseline_close is not None:
        content += (
            f" ({baseline_time.isoformat(timespec='seconds')}, {baseline_close:.4f}"
            f"{currency_suffix}): {movement}. "
        )
    else:
        content += f": {movement}. "
    content += f"Window high: {high}. Window low: {low}.{window_range_text}"
    if api_url:
        content += " Source data URL is the Yahoo Finance chart API URL attached to this evidence item."
    title_price_label = str(instrument.get("price_label", "最新价")).strip() or "最新价"
    title_movement = (
        f"{title_movement_prefix}{direction} {abs(change_pct):.2f}%"
        if change_pct is not None
        else "最新快照"
    )
    raw_tickers = instrument.get("tickers", [])
    if isinstance(raw_tickers, str):
        matched_tickers = [raw_tickers.upper().strip()] if raw_tickers.strip() else []
    else:
        matched_tickers = [str(value).upper().strip() for value in raw_tickers if str(value).strip()]
    return [
        EvidenceItem(
            id="",
            title=f"{name}：{title_price_label} {close:.4f}{currency_suffix}，{title_movement}",
            published_at=published.isoformat(timespec="seconds"),
            source_name="Yahoo Finance",
            source_type=str(instrument.get("source_type", "market_data_aggregator")),
            url=api_url,
            content=content,
            matched_topics=[str(value) for value in instrument.get("topics", [])],
            matched_tickers=matched_tickers,
            evidence_level="summary",
        )
    ]


def yahoo_focus_topic_instruments(sources: Mapping[str, Any]) -> List[Dict[str, Any]]:
    instruments: List[Dict[str, Any]] = []
    for topic in configured_focus_topics(sources):
        topic_name = str(topic.get("name", "")).strip()
        for instrument in topic.get("instruments", []):
            if not isinstance(instrument, Mapping):
                continue
            symbol = str(instrument.get("symbol", "")).strip()
            if not symbol:
                continue
            instruments.append(
                {
                    "symbol": symbol,
                    "name": str(instrument.get("name", symbol)).strip() or symbol,
                    "topics": [str(value) for value in instrument.get("topics", [])],
                    "tickers": [str(value) for value in instrument.get("tickers", [])],
                    "interval": str(instrument.get("interval", "1d")),
                    "price_label": str(instrument.get("price_label", "最新价")),
                    "source_type": "market_data_aggregator",
                    "coverage_category": "focus_topic_snapshot",
                    "coverage_detail": f"重点主题：{topic_name}。",
                }
            )
    return instruments


def yahoo_holding_price_instruments(sources: Mapping[str, Any]) -> List[Dict[str, Any]]:
    instruments: List[Dict[str, Any]] = []
    for holding in configured_portfolio_holdings(sources):
        ticker = str(holding.get("ticker", "")).upper().strip()
        symbol = str(holding.get("symbol", ticker)).strip()
        if not ticker or not symbol:
            continue
        company = str(holding.get("company", ticker)).strip() or ticker
        market = str(holding.get("market", "us_equities"))
        market_label = "A股" if market == "a_share" else "美股"
        instruments.append(
            {
                "symbol": symbol,
                "name": company,
                "topics": [f"{market_label}持仓行情", *[str(value) for value in holding.get("themes", [])]],
                "tickers": [ticker],
                "interval": "1h",
                "price_label": "最新股价",
                "source_type": "market_data_aggregator",
                "coverage_category": "holding_price_snapshot",
                "coverage_detail": f"按已配置{market_label}持仓自动采集行情变化与最新价格。",
            }
        )
    return instruments


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
        str(
            collector_settings.get(
                "user_agent",
                "market-analyzer-agents/0.1 research@example.com",
            )
        ),
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
        yahoo_instruments = list(yahoo_config.get("instruments", []))
        configured_symbols = {
            str(instrument.get("symbol", "")).upper().strip()
            for instrument in yahoo_instruments
            if isinstance(instrument, Mapping)
        }
        for instrument in yahoo_focus_topic_instruments(sources):
            symbol_key = str(instrument.get("symbol", "")).upper().strip()
            if symbol_key and symbol_key not in configured_symbols:
                yahoo_instruments.append(instrument)
                configured_symbols.add(symbol_key)
        if yahoo_config.get("include_us_holding_prices", True):
            for instrument in yahoo_holding_price_instruments(sources):
                symbol_key = str(instrument.get("symbol", "")).upper().strip()
                if symbol_key and symbol_key not in configured_symbols:
                    yahoo_instruments.append(instrument)
                    configured_symbols.add(symbol_key)
        for instrument in yahoo_instruments:
            name = f"Yahoo Finance / {instrument.get('name', instrument.get('symbol', 'unknown'))}"
            category = str(instrument.get("coverage_category", "market_snapshot"))
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
                    category=category,
                    status="collected" if items else "no_items",
                    item_count=len(items),
                    detail=str(instrument.get("coverage_detail", "")),
                )
            except Exception as exc:
                pack.errors.append(f"Yahoo Finance collector failed for {name}: {exc}")
                _record_coverage(
                    pack,
                    name=name,
                    category=category,
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
