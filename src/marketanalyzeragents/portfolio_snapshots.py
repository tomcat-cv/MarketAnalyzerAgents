from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .config import resolve_path
from .portfolio_store import PortfolioStore


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


def overlay_snapshot_holdings(
    sources: Mapping[str, Any],
    snapshots: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    updated = deepcopy(dict(sources))
    portfolios = updated.setdefault("portfolios", {})
    if not isinstance(portfolios, dict):
        portfolios = {}
        updated["portfolios"] = portfolios
    for market, holdings in snapshots.items():
        portfolios.setdefault(market, {})
        if isinstance(portfolios[market], dict):
            portfolios[market]["holdings"] = [dict(item) for item in holdings]
    return updated


def snapshot_holdings_from_store(root: Path, settings: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    if not db_path.exists():
        return {}
    snapshots: dict[str, list[dict[str, Any]]] = {}
    with PortfolioStore(db_path) as store:
        for market in MARKETS:
            snapshot = store.latest_portfolio_snapshot(market)
            if snapshot is not None:
                snapshots[market] = [dict(item) for item in snapshot["holdings"]]
    return snapshots


def apply_current_portfolio_snapshots(
    root: Path,
    settings: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots = snapshot_holdings_from_store(root, settings)
    if not snapshots:
        return dict(sources)
    return overlay_snapshot_holdings(sources, snapshots)


def save_confirmed_snapshot(
    root: Path,
    settings: Mapping[str, Any],
    market: str,
    holdings: Sequence[Mapping[str, Any]],
    *,
    source_type: str,
    source_ref: str = "",
    note: str = "",
) -> int:
    if market not in MARKETS:
        raise ValueError("market must be a_share or us_equities")
    normalized = [normalize_holding(market, item) for item in holdings]
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    created_at = datetime.now(ZoneInfo(str(settings.get("timezone", "Asia/Shanghai")))).isoformat(
        timespec="seconds"
    )
    with PortfolioStore(db_path) as store:
        return store.save_portfolio_snapshot(
            market,
            normalized,
            created_at=created_at,
            source_type=source_type,
            source_ref=source_ref,
            status="confirmed",
            note=note,
        )
