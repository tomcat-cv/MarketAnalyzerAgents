from __future__ import annotations

import json
import math
import os
import statistics
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .collectors import CollectionError, HttpClient
from .portfolio_store import PortfolioStore, PriceBar, Quote


class ConversationPort(Protocol):
    def deliver(self, message: Mapping[str, Any]) -> None: ...


class JsonlConversationPort:
    def __init__(self, path: Path) -> None:
        self.path = path

    def deliver(self, message: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(message), ensure_ascii=False) + "\n")


class FeishuWebhookConversationPort:
    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def deliver(self, message: Mapping[str, Any]) -> None:
        text = format_conversation_message(message)
        payload = json.dumps(
            {"msg_type": "text", "content": {"text": text}},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Feishu webhook returned a non-JSON response.") from exc
        if result.get("StatusCode", result.get("code", 0)) not in {0, None}:
            raise RuntimeError(f"Feishu webhook failed: {result}")


class CompositeConversationPort:
    def __init__(self, ports: Sequence[ConversationPort]) -> None:
        self.ports = tuple(ports)

    def deliver(self, message: Mapping[str, Any]) -> None:
        for port in self.ports:
            port.deliver(message)


def format_conversation_message(message: Mapping[str, Any]) -> str:
    message_type = str(message.get("type", "intraday_suggestion"))
    if message_type == "pre_market_brief":
        market = str(message.get("market") or "unknown")
        output = str(message.get("output", ""))
        url = str(message.get("url") or "")
        link = url or output
        return "\n".join(
            [
                f"盘前简报已生成 [{market}]",
                f"日期：{message.get('date', '')}",
                f"路径：{output}",
                f"链接：{link}" if link else "",
                f"生成时间：{message.get('generated_at', '')}",
            ]
        ).strip()
    if message_type == "intraday_agent_discussion":
        return "\n".join(
            [
                f"盘中讨论记录 [{message.get('market', '')} {message.get('symbol', '')}]",
                f"建议 ID：{message.get('suggestion_id', '')}",
                f"轮次：{len(message.get('turns', [])) if isinstance(message.get('turns'), list) else 0}",
            ]
        ).strip()
    if {"market", "symbol", "action", "rationale"} <= set(message):
        evidence = message.get("evidence_ids", [])
        return "\n".join(
            [
                f"盘中定时分析 [{message.get('market', '')} {message.get('symbol', '')}]",
                f"时间：{message.get('created_at', '')}",
                f"动作：{message.get('action', '')} / 置信度：{message.get('confidence', '')}",
                f"依据：{message.get('rationale', '')}",
                f"证据：{', '.join(str(item) for item in evidence) if evidence else '无'}",
                f"失效条件：{message.get('invalidation', '')}",
            ]
        ).strip()
    return json.dumps(dict(message), ensure_ascii=False, indent=2)


def build_outbox(root: Path, settings: Mapping[str, Any]) -> ConversationPort:
    state = settings.get("state", {})
    ports: list[ConversationPort] = [
        JsonlConversationPort(root / state.get("conversation_outbox", "state/conversation-outbox.jsonl"))
    ]
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if webhook_url:
        ports.append(FeishuWebhookConversationPort(webhook_url))
    return CompositeConversationPort(ports)


@dataclass(frozen=True)
class MarketData:
    quote: Quote
    history: tuple[PriceBar, ...]
    metrics: Mapping[str, float | None]


def yahoo_symbol(market: str, symbol: str) -> str:
    value = symbol.strip().upper()
    if market != "a_share" or "." in value:
        return value
    if len(value) != 6 or not value.isdigit():
        raise ValueError(f"Unsupported A-share symbol: {symbol}")
    if value[0] in {"5", "6", "9"}:
        return f"{value}.SS"
    if value[0] in {"0", "1", "2", "3"}:
        return f"{value}.SZ"
    if value[0] in {"4", "8"}:
        return f"{value}.BJ"
    raise ValueError(f"Cannot determine exchange for A-share symbol: {symbol}")


def _fetch_yahoo_chart(
    client: HttpClient,
    market: str,
    symbol: str,
    *,
    interval: str,
    range_value: str,
) -> tuple[list[PriceBar], Mapping[str, Any]]:
    provider_symbol = yahoo_symbol(market, symbol)
    encoded = urllib.parse.quote(provider_symbol, safe="")
    query = urllib.parse.urlencode({"interval": interval, "range": range_value})
    errors = []
    result = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}?{query}"
        try:
            payload = client.get_json(url)
        except CollectionError as exc:
            errors.append(f"{host}: {exc}")
            continue
        results = payload.get("chart", {}).get("result") or []
        if results:
            result = results[0]
            break
        errors.append(f"{host}: {payload.get('chart', {}).get('error')}")
    if result is None:
        raise ValueError(f"No market data returned for {provider_symbol}; {'; '.join(errors)}")
    timestamps = result.get("timestamp") or []
    values = (result.get("indicators", {}).get("quote") or [{}])[0]
    bars = []
    for index, timestamp in enumerate(timestamps):
        row = {key: (values.get(key) or []) for key in ("open", "high", "low", "close", "volume")}
        if any(index >= len(row[key]) or row[key][index] is None for key in ("open", "high", "low", "close")):
            continue
        bars.append(
            PriceBar(
                market=market,
                symbol=symbol,
                interval=interval,
                observed_at=datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                open=float(row["open"][index]),
                high=float(row["high"][index]),
                low=float(row["low"][index]),
                close=float(row["close"][index]),
                volume=float(row["volume"][index])
                if index < len(row["volume"]) and row["volume"][index] is not None
                else None,
                source="Yahoo Finance (unofficial)",
            )
        )
    if not bars:
        raise ValueError(f"No usable market data returned for {provider_symbol}")
    return bars, result.get("meta", {})


def fetch_yahoo_bars(
    client: HttpClient,
    market: str,
    symbol: str,
    *,
    interval: str,
    range_value: str,
) -> list[PriceBar]:
    bars, _ = _fetch_yahoo_chart(
        client, market, symbol, interval=interval, range_value=range_value
    )
    return bars


def price_metrics(history: Sequence[PriceBar]) -> dict[str, float | None]:
    closes = [bar.close for bar in history]
    if len(closes) < 2:
        return {"period_change_pct": None, "annualized_volatility_pct": None, "max_drawdown_pct": None}
    returns = [(current / previous) - 1 for previous, current in zip(closes, closes[1:]) if previous]
    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, (close / peak) - 1)
    volatility = statistics.stdev(returns) * math.sqrt(252) * 100 if len(returns) > 1 else None
    return {
        "period_change_pct": round(((closes[-1] / closes[0]) - 1) * 100, 4),
        "annualized_volatility_pct": round(volatility, 4) if volatility is not None else None,
        "max_drawdown_pct": round(max_drawdown * 100, 4),
    }


def fetch_yahoo_market_data(
    client: HttpClient,
    market: str,
    symbol: str,
    *,
    history_range: str = "6mo",
    history_interval: str = "1d",
) -> MarketData:
    intraday, intraday_meta = _fetch_yahoo_chart(
        client, market, symbol, interval="1m", range_value="1d"
    )
    history, _ = _fetch_yahoo_chart(
        client, market, symbol, interval=history_interval, range_value=history_range
    )
    latest = intraday[-1]
    raw_previous_close = intraday_meta.get("chartPreviousClose")
    previous_close = (
        float(raw_previous_close)
        if raw_previous_close is not None
        else history[-2].close if len(history) > 1 else None
    )
    raw_volume = intraday_meta.get("regularMarketVolume")
    return MarketData(
        quote=Quote(
            market=market,
            symbol=symbol,
            observed_at=latest.observed_at,
            price=latest.close,
            previous_close=previous_close,
            volume=float(raw_volume) if raw_volume is not None else latest.volume,
            source=latest.source,
        ),
        history=tuple(history),
        metrics=price_metrics(history),
    )


def market_history_payload(data: MarketData, limit: int) -> list[dict[str, Any]]:
    rows = [asdict(bar) for bar in data.history[-limit:]]
    rows.append({"type": "history_metrics", **data.metrics})
    return rows


def fetch_yahoo_quote(client: HttpClient, market: str, symbol: str) -> Quote:
    return fetch_yahoo_market_data(client, market, symbol).quote


def build_suggestion(store: PortfolioStore, quote: Quote, evidence_ids: Sequence[str] = ()) -> dict[str, Any]:
    rows = store.recent_quotes(quote.market, quote.symbol, 2)
    baseline = quote.previous_close
    if len(rows) > 1:
        baseline = float(rows[1]["price"])
    change_pct = ((quote.price / baseline) - 1) * 100 if baseline else 0.0
    material = abs(change_pct) >= 2.0
    return {
        "market": quote.market,
        "symbol": quote.symbol,
        "created_at": quote.observed_at,
        "action": "观察",
        "confidence": "中" if material and evidence_ids else "低",
        "rationale": (
            f"价格相对可比基准变化 {change_pct:+.2f}%。"
            + ("存在已验证资讯，需人工或模型结合持仓约束判断。" if evidence_ids else "缺少同期已验证资讯，不生成买卖方向。")
        ),
        "evidence_ids": list(evidence_ids),
        "invalidation": "行情数据过期、交易时段结束或出现新的公司/宏观证据时重新评估。",
        "price_change_pct": round(change_pct, 4),
        "signal": "material_price_move" if material else "routine_poll",
    }


def should_run_agent_debate(suggestion: Mapping[str, Any]) -> bool:
    """Only spend model calls on events that can change a user's decision."""
    return bool(suggestion.get("evidence_ids")) or suggestion.get("signal") == "material_price_move"


def should_emit_suggestion(
    suggestion: Mapping[str, Any],
    *,
    emit_low_signal: bool = False,
) -> bool:
    """Suppress routine low-signal observations while keeping real alerts auditable."""
    if emit_low_signal:
        return True
    if suggestion.get("action") != "观察":
        return True
    if suggestion.get("confidence") != "低":
        return True
    if suggestion.get("evidence_ids"):
        return True
    return suggestion.get("signal") == "material_price_move"
