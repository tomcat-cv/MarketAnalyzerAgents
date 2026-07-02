from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .collectors import HttpClient
from .config import resolve_path
from .portfolio_snapshots import MARKETS, normalize_holding
from .portfolio_store import PortfolioStore


def parse_holdings_from_text(market: str, text: str) -> list[dict[str, Any]]:
    holdings = []
    for line in text.splitlines():
        parts = [part.strip() for part in re.split(r"[\t,，|]+", line) if part.strip()]
        if not parts or parts[0].lower() in {"ticker", "symbol", "代码", "证券代码"}:
            continue
        payload: dict[str, Any] = {"ticker": parts[0]}
        if len(parts) > 1:
            payload["company"] = parts[1]
        if len(parts) > 2 and re.fullmatch(r"-?\d+(\.\d+)?", parts[2]):
            payload["quantity"] = parts[2]
        if len(parts) > 3 and re.fullmatch(r"-?\d+(\.\d+)?", parts[3]):
            payload["cost_basis"] = parts[3]
        try:
            holdings.append(normalize_holding(market, payload))
        except ValueError:
            continue
    return holdings


def _store_image(root: Path, settings: Mapping[str, Any], payload: Mapping[str, Any], event_id: str) -> str:
    state_dir = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db")).parent
    image_dir = state_dir / "feishu-imports"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{event_id}.bin"
    if payload.get("image_base64"):
        image_path.write_bytes(base64.b64decode(str(payload["image_base64"])))
        return str(image_path)
    image_url = str(payload.get("image_url", "")).strip()
    if image_url:
        collector_settings = settings.get("collectors", {})
        if not isinstance(collector_settings, Mapping):
            collector_settings = {}
        client = HttpClient(
            str(collector_settings.get("user_agent", "market-analyzer-agents/0.1")),
            int(collector_settings.get("timeout_seconds", 30)),
            int(collector_settings.get("max_retries", 2)),
            float(collector_settings.get("retry_backoff_seconds", 1.0)),
        )
        image_path.write_bytes(client.request_bytes(image_url))
        return str(image_path)
    return ""


def create_feishu_portfolio_import(
    root: Path,
    settings: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    market = str(payload.get("market", "")).strip()
    if market not in MARKETS:
        raise ValueError("market must be a_share or us_equities")
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id:
        raise ValueError("event_id is required")
    text = str(payload.get("ocr_text", "")).strip()
    parsed_payload = payload.get("holdings", [])
    if isinstance(parsed_payload, list) and parsed_payload:
        holdings = [normalize_holding(market, item) for item in parsed_payload if isinstance(item, Mapping)]
    else:
        holdings = parse_holdings_from_text(market, text)
    status = "pending_confirmation" if holdings else "needs_ocr"
    image_path = _store_image(root, settings, payload, event_id)
    received_at = datetime.now(ZoneInfo(str(settings.get("timezone", "Asia/Shanghai")))).isoformat(
        timespec="seconds"
    )
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    with PortfolioStore(db_path) as store:
        import_id = store.save_feishu_import(
            event_id=event_id,
            market=market,
            received_at=received_at,
            status=status,
            image_path=image_path,
            ocr_text=text,
            parsed_holdings=holdings,
        )
    return {
        "id": import_id,
        "event_id": event_id,
        "market": market,
        "status": status,
        "image_path": image_path,
        "holdings": holdings,
    }


def confirm_feishu_portfolio_import(root: Path, settings: Mapping[str, Any], import_id: int) -> dict[str, Any]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    created_at = datetime.now(ZoneInfo(str(settings.get("timezone", "Asia/Shanghai")))).isoformat(
        timespec="seconds"
    )
    with PortfolioStore(db_path) as store:
        snapshot_id = store.confirm_feishu_import(import_id, created_at)
        row = store.feishu_import(import_id)
        holdings = json.loads(row["parsed_holdings_json"]) if row is not None else []
    return {"id": import_id, "snapshot_id": snapshot_id, "status": "confirmed", "holdings": holdings}
