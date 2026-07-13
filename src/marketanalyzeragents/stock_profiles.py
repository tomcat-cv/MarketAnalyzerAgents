from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from .collectors_core import HttpClient
from .intraday import yahoo_symbol


def _first_equity(payload: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    expected = symbol.upper()
    for item in payload.get("quotes", []):
        if not isinstance(item, Mapping) or str(item.get("quoteType", "")).upper() != "EQUITY":
            continue
        candidate = str(item.get("symbol", "")).upper()
        if candidate == expected:
            return item
    raise ValueError(f"未找到真实股票代码 {symbol}")


def _search(client: HttpClient, symbol: str, lang: str, region: str) -> Mapping[str, Any]:
    query = urllib.parse.urlencode(
        {"q": symbol, "quotesCount": 8, "newsCount": 0, "lang": lang, "region": region}
    )
    return client.get_json(f"https://query2.finance.yahoo.com/v1/finance/search?{query}")


def lookup_stock_profile(client: HttpClient, market: str, ticker: str) -> dict[str, Any]:
    if market not in {"a_share", "us_equities"}:
        raise ValueError("market must be a_share or us_equities")
    raw_ticker = ticker.strip().upper()
    if not raw_ticker:
        raise ValueError("股票代码不能为空")
    symbol = yahoo_symbol(market, raw_ticker)
    english = _first_equity(_search(client, symbol, "en-US", "US"), symbol)
    localized = _first_equity(_search(client, symbol, "zh-Hant", "HK"), symbol)

    exchange = str(english.get("exchange", "")).upper()
    if market == "a_share" and not symbol.endswith((".SS", ".SZ", ".BJ")):
        raise ValueError(f"{ticker} 不是有效的 A 股代码")
    if market == "us_equities" and exchange not in {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"}:
        raise ValueError(f"{ticker} 不是支持的美股代码")

    name_en = str(english.get("longname") or english.get("shortname") or raw_ticker).strip()
    name_zh = str(localized.get("longname") or localized.get("shortname") or name_en).strip()
    sectors = []
    for item in (
        localized.get("sector"), localized.get("industry"),
        english.get("sector"), english.get("industry"),
    ):
        value = str(item or "").strip()
        if value and value.casefold() not in {entry.casefold() for entry in sectors}:
            sectors.append(value)
    if not sectors:
        raise ValueError(f"已确认 {ticker} 为股票，但资料源未返回业务领域，请稍后重试")

    encoded = urllib.parse.quote(raw_ticker, safe="")
    if market == "us_equities":
        official_sources = [
            {"name": "SEC EDGAR", "url": f"https://www.sec.gov/edgar/browse/?CIK={encoded}&owner=exclude", "type": "disclosure"},
        ]
    else:
        exchange_url = "https://www.sse.com.cn/assortment/stock/list/info/announcement/" if symbol.endswith(".SS") else "https://www.szse.cn/disclosure/listed/notice/index.html"
        official_sources = [
            {"name": "巨潮资讯", "url": f"https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord={encoded}", "type": "disclosure"},
            {"name": "交易所公告", "url": exchange_url, "type": "disclosure"},
        ]
    return {
        "ticker": raw_ticker,
        "symbol": symbol,
        "company_name_zh": name_zh,
        "company_name_en": name_en,
        "company": name_zh if market == "a_share" else name_en,
        "business_domains": sectors,
        "themes": sectors,
        "official_sources": official_sources,
        "verified": True,
    }
