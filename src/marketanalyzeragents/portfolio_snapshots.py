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
    for key in ("quantity", "cost_basis"):
        if payload.get(key) not in (None, ""):
            result[key] = float(payload[key])
    currency = str(payload.get("currency", "")).strip()
    if currency:
        result["currency"] = currency
    return result
