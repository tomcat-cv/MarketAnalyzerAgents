from __future__ import annotations

import math
import statistics
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from .collectors import CollectionError, HttpClient


@dataclass(frozen=True)
class Quote:
    market: str
    symbol: str
    observed_at: str
    price: float
    previous_close: float | None = None
    volume: float | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class PriceBar:
    market: str
    symbol: str
    interval: str
    observed_at: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class MarketData:
    quote: Quote
    history: tuple[PriceBar, ...]
    metrics: Mapping[str, float | None]


class MarketDataProviderError(ValueError):
    pass


class MarketDataProvider(Protocol):
    def fetch(self, market: str, symbol: str) -> MarketData: ...


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


class YahooMarketDataProvider:
    def __init__(
        self,
        client: HttpClient,
        *,
        history_range: str = "6mo",
        history_interval: str = "1d",
    ) -> None:
        self.client = client
        self.history_range = history_range
        self.history_interval = history_interval

    def fetch(self, market: str, symbol: str) -> MarketData:
        return fetch_yahoo_market_data(
            self.client,
            market,
            symbol,
            history_range=self.history_range,
            history_interval=self.history_interval,
        )


def build_market_data_provider(client: HttpClient, settings: Mapping[str, Any]) -> MarketDataProvider:
    provider = str(settings.get("provider", "yahoo")).strip().lower()
    if provider == "yahoo":
        return YahooMarketDataProvider(
            client,
            history_range=str(settings.get("history_range", "6mo")),
            history_interval=str(settings.get("history_interval", "1d")),
        )
    raise MarketDataProviderError(
        f"Unsupported market-data provider: {provider or '(empty)'}. "
        "Configured provider must be yahoo until another provider adapter is added."
    )


def fetch_market_data(
    client: HttpClient,
    market: str,
    symbol: str,
    settings: Mapping[str, Any],
) -> MarketData:
    return build_market_data_provider(client, settings).fetch(market, symbol)
