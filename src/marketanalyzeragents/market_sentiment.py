from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .collectors_core import HttpClient
from .intraday import PriceBar, _fetch_yahoo_chart


CBOE_EQUITY_PUT_CALL_URL = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv"
FRED_HIGH_YIELD_SPREAD_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"


@dataclass(frozen=True)
class SentimentComponent:
    key: str
    name: str
    group: str
    weight: float
    status: str
    value: float | None = None
    unit: str = ""
    score: float | None = None
    observed_at: str = ""
    source: str = ""
    analysis: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "group": self.group,
            "weight": self.weight,
            "status": self.status,
            "value": self.value,
            "unit": self.unit,
            "score": round(self.score, 2) if self.score is not None else None,
            "observed_at": self.observed_at,
            "source": self.source,
            "analysis": self.analysis,
            "error": self.error,
        }


def sentiment_label(score: float) -> str:
    if score <= 24:
        return "Extreme Fear"
    if score <= 44:
        return "Fear"
    if score <= 55:
        return "Neutral"
    if score <= 75:
        return "Greed"
    return "Extreme Greed"


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_low_is_greedy(value: float, fear_level: float, greed_level: float) -> float:
    return _clamp((fear_level - value) / (fear_level - greed_level) * 100)


def _score_high_is_greedy(value: float, fear_level: float, greed_level: float) -> float:
    return _clamp((value - fear_level) / (greed_level - fear_level) * 100)


def _latest_close(bars: list[PriceBar]) -> PriceBar:
    if not bars:
        raise ValueError("missing price history")
    return bars[-1]


def _unavailable(key: str, name: str, group: str, weight: float, error: Exception | str) -> SentimentComponent:
    return SentimentComponent(
        key=key,
        name=name,
        group=group,
        weight=weight,
        status="unavailable",
        error=str(error),
    )


def _fetch_daily_history(client: HttpClient, symbol: str, range_value: str = "1y") -> list[PriceBar]:
    bars, _ = _fetch_yahoo_chart(
        client,
        "us_equities",
        symbol,
        interval="1d",
        range_value=range_value,
    )
    return bars


def _component_vix(client: HttpClient) -> SentimentComponent:
    bars = _fetch_daily_history(client, "^VIX", "6mo")
    latest = _latest_close(bars)
    value = latest.close
    score = _score_low_is_greedy(value, fear_level=35, greed_level=12)
    return SentimentComponent(
        key="vix",
        name="VIX",
        group="短期情绪",
        weight=0.20,
        status="ok",
        value=round(value, 2),
        unit="",
        score=score,
        observed_at=latest.observed_at,
        source="Yahoo Finance / Cboe index",
        analysis="VIX 越高代表期权市场隐含波动越高，短期恐惧越强。",
    )


def _component_vvix(client: HttpClient) -> SentimentComponent:
    bars = _fetch_daily_history(client, "^VVIX", "6mo")
    latest = _latest_close(bars)
    value = latest.close
    score = _score_low_is_greedy(value, fear_level=130, greed_level=75)
    return SentimentComponent(
        key="vvix",
        name="VVIX",
        group="短期情绪",
        weight=0.10,
        status="ok",
        value=round(value, 2),
        score=score,
        observed_at=latest.observed_at,
        source="Yahoo Finance / Cboe index",
        analysis="VVIX 衡量 VIX 自身波动预期，越高说明波动风险升温。",
    )


def _parse_cboe_put_call(text: str) -> tuple[float, str]:
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(index for index, row in enumerate(rows) if row[:5] == ["DATE", "CALL", "PUT", "TOTAL", "P/C Ratio"])
    latest = None
    for row in rows[header_index + 1 :]:
        if len(row) >= 5 and row[0].strip() and row[4].strip():
            latest = row
    if latest is None:
        raise ValueError("missing CBOE equity put/call row")
    return float(latest[4]), latest[0].strip()


def _component_equity_put_call(client: HttpClient, url: str = CBOE_EQUITY_PUT_CALL_URL) -> SentimentComponent:
    ratio, date_text = _parse_cboe_put_call(client.get_text(url))
    score = _score_low_is_greedy(ratio, fear_level=0.95, greed_level=0.45)
    return SentimentComponent(
        key="equity_put_call",
        name="CBOE Equity Put/Call Ratio",
        group="短期情绪",
        weight=0.10,
        status="ok",
        value=round(ratio, 4),
        score=score,
        observed_at=date_text,
        source="Cboe Equity Put/Call Ratio CSV",
        analysis="Put/Call 越高代表防御性期权需求越强，偏恐惧。",
    )


def _parse_fred_latest(text: str, column: str) -> tuple[float, str]:
    latest = None
    for row in csv.DictReader(io.StringIO(text)):
        value = (row.get(column) or "").strip()
        if value and value != ".":
            latest = (float(value), str(row.get("observation_date", "")).strip())
    if latest is None:
        raise ValueError(f"missing FRED series value: {column}")
    return latest


def _component_high_yield_spread(client: HttpClient, url: str = FRED_HIGH_YIELD_SPREAD_URL) -> SentimentComponent:
    value, observed_at = _parse_fred_latest(client.get_text(url), "BAMLH0A0HYM2")
    score = _score_low_is_greedy(value, fear_level=8.0, greed_level=2.5)
    return SentimentComponent(
        key="high_yield_spread",
        name="美国高收益债利差",
        group="中期风险偏好",
        weight=0.15,
        status="ok",
        value=round(value, 2),
        unit="%",
        score=score,
        observed_at=observed_at,
        source="FRED BAMLH0A0HYM2",
        analysis="垃圾债利差越宽，信用风险溢价越高，风险偏好越弱。",
    )


def _component_spx_trend(client: HttpClient) -> SentimentComponent:
    bars = _fetch_daily_history(client, "^GSPC", "1y")
    closes = [bar.close for bar in bars]
    if len(closes) < 200:
        raise ValueError("S&P 500 history is shorter than 200 sessions")
    latest = _latest_close(bars)
    ma200 = statistics.fmean(closes[-200:])
    distance = (latest.close / ma200 - 1) * 100
    score = _score_high_is_greedy(distance, fear_level=-8.0, greed_level=8.0)
    return SentimentComponent(
        key="spx_200d_trend",
        name="S&P 500 相对 200 日均线",
        group="趋势因子",
        weight=0.20,
        status="ok",
        value=round(distance, 2),
        unit="%",
        score=score,
        observed_at=latest.observed_at,
        source="Yahoo Finance ^GSPC",
        analysis="指数高于长期均线越多，趋势风险偏好越强；跌破均线则偏防御。",
    )


def _component_equity_treasury_relative(client: HttpClient) -> SentimentComponent:
    spy = _fetch_daily_history(client, "SPY", "6mo")
    tlt = _fetch_daily_history(client, "TLT", "6mo")
    lookback = min(63, len(spy) - 1, len(tlt) - 1)
    if lookback < 20:
        raise ValueError("SPY/TLT history is too short")
    spy_return = spy[-1].close / spy[-lookback - 1].close - 1
    tlt_return = tlt[-1].close / tlt[-lookback - 1].close - 1
    relative = (spy_return - tlt_return) * 100
    score = _score_high_is_greedy(relative, fear_level=-8.0, greed_level=8.0)
    return SentimentComponent(
        key="equity_treasury_relative",
        name="股票相对美债表现",
        group="资金流向",
        weight=0.10,
        status="ok",
        value=round(relative, 2),
        unit="pct",
        score=score,
        observed_at=spy[-1].observed_at,
        source="Yahoo Finance SPY/TLT",
        analysis="股票相对长期美债跑赢，通常代表资金更偏风险资产。",
    )


def _component_market_breadth_unavailable() -> SentimentComponent:
    return _unavailable(
        "advance_decline",
        "市场宽度 Advance/Decline",
        "中期风险偏好",
        0.10,
        "尚未配置稳定的交易所市场宽度数据源",
    )


def _component_new_high_low_unavailable() -> SentimentComponent:
    return _unavailable(
        "new_high_low",
        "52 周新高/新低",
        "中期风险偏好",
        0.05,
        "尚未配置稳定的 52 周新高/新低数据源",
    )


def _component_etf_flow_unavailable() -> SentimentComponent:
    return _unavailable(
        "etf_flow",
        "ETF 资金流",
        "资金流向",
        0.10,
        "尚未配置稳定的 ETF 净流入数据源",
    )


def collect_market_sentiment(client: HttpClient, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    component_builders = [
        ("vix", _component_vix),
        ("vvix", _component_vvix),
        ("equity_put_call", _component_equity_put_call),
        ("high_yield_spread", _component_high_yield_spread),
        ("advance_decline", lambda _client: _component_market_breadth_unavailable()),
        ("new_high_low", lambda _client: _component_new_high_low_unavailable()),
        ("spx_200d_trend", _component_spx_trend),
        ("equity_treasury_relative", _component_equity_treasury_relative),
        ("etf_flow", lambda _client: _component_etf_flow_unavailable()),
    ]
    components: list[SentimentComponent] = []
    for _, builder in component_builders:
        try:
            components.append(builder(client))
        except Exception as exc:
            key, name, group, weight = _component_metadata(builder)
            components.append(_unavailable(key, name, group, weight, exc))
    available = [item for item in components if item.status == "ok" and item.score is not None]
    weight_total = sum(item.weight for item in available)
    if weight_total:
        score = sum(float(item.score) * item.weight for item in available if item.score is not None) / weight_total
        status = "ok" if len(available) == len(components) else "partial"
    else:
        score = None
        status = "unavailable"
    rounded_score = round(score) if score is not None else None
    return {
        "value": str(rounded_score) if rounded_score is not None else "",
        "score": rounded_score,
        "label": sentiment_label(float(rounded_score)) if rounded_score is not None else "暂不可用",
        "status": status,
        "available_weight": round(weight_total, 4),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": _summary(rounded_score, components),
        "components": [item.as_dict() for item in components],
    }


def _component_metadata(builder: Any) -> tuple[str, str, str, float]:
    name = getattr(builder, "__name__", "")
    fallback = {
        "_component_vix": ("vix", "VIX", "短期情绪", 0.20),
        "_component_vvix": ("vvix", "VVIX", "短期情绪", 0.10),
        "_component_equity_put_call": ("equity_put_call", "CBOE Equity Put/Call Ratio", "短期情绪", 0.10),
        "_component_high_yield_spread": ("high_yield_spread", "美国高收益债利差", "中期风险偏好", 0.15),
        "_component_spx_trend": ("spx_200d_trend", "S&P 500 相对 200 日均线", "趋势因子", 0.20),
        "_component_equity_treasury_relative": ("equity_treasury_relative", "股票相对美债表现", "资金流向", 0.10),
    }
    return fallback.get(name, ("unknown", "未知指标", "未分类", 0.0))


def _summary(score: int | None, components: list[SentimentComponent]) -> str:
    if score is None:
        return "市场情绪数据暂不可用；需要检查行情、CBOE 或 FRED 数据源。"
    label = sentiment_label(float(score))
    available = [item for item in components if item.status == "ok"]
    unavailable = [item for item in components if item.status != "ok"]
    leaders = sorted(available, key=lambda item: abs(float(item.score or 50) - 50), reverse=True)[:3]
    leader_text = "；".join(f"{item.name} {round(float(item.score or 0))}" for item in leaders)
    missing_text = f"；{len(unavailable)} 个分项暂不可用，已按可用权重重算。" if unavailable else ""
    return f"综合情绪为 {score} / {label}。主要驱动：{leader_text or '暂无'}{missing_text}"
