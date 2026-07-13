from __future__ import annotations

from typing import Any, Mapping


MARKETS = ("a_share", "us_equities")


def normalize_holding(market: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    ticker = str(payload.get("ticker", "")).strip()
    if market == "us_equities":
        ticker = ticker.upper()
    if not ticker:
        raise ValueError("ticker is required")
    symbol = str(payload.get("symbol", ticker)).strip() or ticker
    if market == "us_equities":
        symbol = symbol.upper()
    company = str(payload.get("company", ticker)).strip() or ticker
    raw_themes = payload.get("themes", [])
    if isinstance(raw_themes, str):
        raw_themes = raw_themes.splitlines()
    if not isinstance(raw_themes, list):
        raise ValueError("themes must be a list or newline-delimited string")
    result: dict[str, Any] = {
        "ticker": ticker,
        "symbol": symbol,
        "company": company,
        "themes": [str(value).strip() for value in raw_themes if str(value).strip()],
    }
    for key in ("company_name_zh", "company_name_en"):
        value = str(payload.get(key, "")).strip()
        if value:
            result[key] = value
    domains = payload.get("business_domains", result["themes"])
    if isinstance(domains, list):
        result["business_domains"] = [str(value).strip() for value in domains if str(value).strip()]
    official_sources = payload.get("official_sources", [])
    if isinstance(official_sources, list):
        result["official_sources"] = [dict(value) for value in official_sources if isinstance(value, Mapping)]
    result["verified"] = bool(payload.get("verified", False))
    for key in ("quantity", "cost_basis"):
        if payload.get(key) not in (None, ""):
            number = float(payload[key])
            if number < 0:
                raise ValueError(f"{key} must be non-negative")
            result[key] = number
    currency = str(payload.get("currency", "")).strip()
    result["currency"] = currency or ("USD" if market == "us_equities" else "CNY")
    return result
