from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .openai_runner import run_openai
from .portfolio_store import Quote
from .zhipu_runner import run_zhipu


AgentInvoker = Callable[[str, str, str], str]


@dataclass(frozen=True)
class DebateTurn:
    role: str
    round: int
    content: str


@dataclass(frozen=True)
class DebateResult:
    suggestion: dict[str, Any]
    turns: list[DebateTurn]


ROLE_INSTRUCTIONS = {
    "market_analyst": "分析价格、成交量、数据新鲜度和可比基准，不推测未提供的原因。",
    "news_analyst": "分析已验证资讯及其与标的的关系，明确证据缺口。",
    "bull_researcher": "提出最强的看多论点，并直接回应已有看空论点。",
    "bear_researcher": "提出最强的风险论点，并直接回应已有看多论点。",
    "risk_manager": "审查双方是否忽略数据过期、证据不足、波动和失效条件。",
    "portfolio_manager": "综合讨论并按指定 JSON 结构给出最终裁决。",
}


def backend_invoker(settings: Mapping[str, Any], backend: str) -> AgentInvoker:
    def invoke(role: str, system: str, user: str) -> str:
        if backend == "openai":
            result, _ = run_openai(settings=settings, system=system, user=user)
        elif backend == "zhipu":
            result, _ = run_zhipu(settings=settings, system=system, user=user)
        else:
            raise ValueError("agent backend must be openai or zhipu")
        return result.text

    return invoke


def _clean_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(value)


def run_agent_debate(
    *,
    quote: Quote,
    evidence: Sequence[Mapping[str, Any]],
    invoke: AgentInvoker,
    rounds: int = 1,
    price_history: Sequence[Mapping[str, Any]] = (),
    portfolio: Mapping[str, Any] | None = None,
) -> DebateResult:
    if rounds < 1 or rounds > 3:
        raise ValueError("debate rounds must be between 1 and 3")
    evidence_ids = {str(item["id"]) for item in evidence}
    market_context = {
        "quote": quote.__dict__,
        "price_history": list(price_history),
        "portfolio": dict(portfolio or {}),
        "constraints": [
            "只能使用给定行情和证据",
            "不得给出仓位比例、目标价或自动交易指令",
            "证据不足时不得给出加仓或减仓",
        ],
    }
    research_context = {**market_context, "evidence": list(evidence)}
    turns: list[DebateTurn] = []

    def ask(role: str, round_number: int, context: Mapping[str, Any]) -> str:
        content = invoke(
            role,
            f"你是盘中多Agent讨论中的{role}。{ROLE_INSTRUCTIONS[role]}",
            json.dumps(context, ensure_ascii=False),
        ).strip()
        turns.append(DebateTurn(role, round_number, content))
        return content

    market_view = ask("market_analyst", 0, market_context)
    news_view = ask("news_analyst", 0, research_context)
    transcript = ""
    for round_number in range(1, rounds + 1):
        bull = ask(
            "bull_researcher",
            round_number,
            {
                **research_context,
                "market_view": market_view,
                "news_view": news_view,
                "debate": transcript,
            },
        )
        transcript += f"\nBull: {bull}"
        bear = ask(
            "bear_researcher",
            round_number,
            {
                **research_context,
                "market_view": market_view,
                "news_view": news_view,
                "debate": transcript,
            },
        )
        transcript += f"\nBear: {bear}"
    risk = ask(
        "risk_manager",
        rounds + 1,
        {
            **research_context,
            "market_view": market_view,
            "news_view": news_view,
            "debate": transcript,
        },
    )
    final_text = ask(
        "portfolio_manager",
        rounds + 2,
        {
            **research_context,
            "market_view": market_view,
            "news_view": news_view,
            "debate": transcript,
            "risk_review": risk,
            "required_shape": {
                "action": "加仓|减仓|持有|观察",
                "confidence": "低|中|高",
                "rationale": "string",
                "evidence_ids": ["EVID-001"],
                "invalidation": "string",
            },
        },
    )
    payload = _clean_json(final_text)
    if payload.get("action") not in {"加仓", "减仓", "持有", "观察"}:
        raise ValueError("portfolio manager returned an invalid action")
    if payload.get("confidence") not in {"低", "中", "高"}:
        raise ValueError("portfolio manager returned invalid confidence")
    cited = [str(value) for value in payload.get("evidence_ids", [])]
    if not set(cited).issubset(evidence_ids):
        raise ValueError("portfolio manager cited evidence that was not supplied")
    if payload["action"] in {"加仓", "减仓"} and not cited:
        raise ValueError("directional advice requires supplied evidence")
    return DebateResult(
        suggestion={
            "market": quote.market,
            "symbol": quote.symbol,
            "created_at": quote.observed_at,
            "action": payload["action"],
            "confidence": payload["confidence"],
            "rationale": str(payload.get("rationale", "")),
            "evidence_ids": cited,
            "invalidation": str(payload.get("invalidation", "")),
        },
        turns=turns,
    )
