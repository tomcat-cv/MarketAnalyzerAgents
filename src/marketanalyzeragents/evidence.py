from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


@dataclass
class EvidenceItem:
    id: str
    title: str
    published_at: str
    source_name: str
    source_type: str
    url: str
    content: str
    matched_topics: list[str] = field(default_factory=list)
    matched_tickers: list[str] = field(default_factory=list)
    evidence_level: str = "summary"
    display_url: str = ""


def configured_us_holdings(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_holdings = sources.get("portfolios", {}).get("us_equities", {}).get("holdings", [])
    if not isinstance(raw_holdings, list):
        return []
    holdings: list[dict[str, Any]] = []
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
                "symbol": str(entry.get("symbol", ticker)).strip() or ticker,
                "company": str(entry.get("company", ticker)).strip() or ticker,
                "company_name_zh": str(entry.get("company_name_zh", "")).strip(),
                "company_name_en": str(entry.get("company_name_en", "")).strip(),
                "themes": [str(value) for value in entry.get("themes", [])],
                "quantity": float(entry["quantity"]) if entry.get("quantity") not in (None, "") else None,
                "cost_basis": float(entry["cost_basis"]) if entry.get("cost_basis") not in (None, "") else None,
                "currency": str(entry.get("currency", "USD")).strip() or "USD",
            }
        )
    return holdings


def configured_a_share_holdings(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_holdings = sources.get("portfolios", {}).get("a_share", {}).get("holdings", [])
    if not isinstance(raw_holdings, list):
        return []
    holdings: list[dict[str, Any]] = []
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
                "symbol": str(entry.get("symbol", ticker)).strip() or ticker,
                "company": str(entry.get("company", ticker)).strip() or ticker,
                "company_name_zh": str(entry.get("company_name_zh", "")).strip(),
                "company_name_en": str(entry.get("company_name_en", "")).strip(),
                "themes": [str(value) for value in entry.get("themes", [])],
                "quantity": float(entry["quantity"]) if entry.get("quantity") not in (None, "") else None,
                "cost_basis": float(entry["cost_basis"]) if entry.get("cost_basis") not in (None, "") else None,
                "currency": str(entry.get("currency", "CNY")).strip() or "CNY",
            }
        )
    return holdings


def configured_portfolio_holdings(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    for market, values in [
        ("a_share", configured_a_share_holdings(sources)),
        ("us_equities", configured_us_holdings(sources)),
    ]:
        for holding in values:
            copied = dict(holding)
            copied["market"] = market
            holdings.append(copied)
    return holdings


def dedupe_evidence(items: list[EvidenceItem], max_items: int) -> list[EvidenceItem]:
    seen: set[tuple[str, str]] = set()
    result: list[EvidenceItem] = []
    for item in items:
        key = (_canonical_url(item.url), item.title.casefold().strip())
        if key in seen:
            continue
        seen.add(key)
        copied = EvidenceItem(**item.__dict__)
        copied.id = f"EVID-{len(result) + 1:03d}"
        result.append(copied)
        if len(result) >= max_items:
            break
    return result


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
