from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from .evidence import configured_portfolio_holdings


def configured_guidance(settings: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    sections = []
    if str(inputs.get("context", "")).strip():
        sections.append(("Research context and output preferences", str(inputs["context"]).strip()))
    if not sections:
        return ""

    content = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    return content[:6000].strip()


def build_openai_messages(
    *,
    settings: Mapping[str, Any],
    inputs: Mapping[str, Any],
    run_date: date,
    evidence_markdown: str = "",
) -> tuple[str, str]:
    holdings = configured_portfolio_holdings(inputs.get("sources", {}))
    holdings_json = json.dumps(holdings, ensure_ascii=False, indent=2)
    guidance = configured_guidance(settings, inputs)
    guidance_block = (
        "\nConfigured guidance below may shape priorities, style, and risk framing only. "
        "It is not factual evidence and must not support factual claims or portfolio actions.\n\n"
        f"{guidance}\n"
        if guidance
        else ""
    )
    system = f"""You are a strict evidence-grounded market research analyst.

Work in {settings.get("language", "zh-CN")} and perform exactly three tasks:
1. Read all supplied evidence as one packet and produce a small set of aggregated market summaries.
2. Give one conservative action assessment for every configured holding.
3. Cite the evidence IDs that directly support each summary and action.

For market summaries, synthesize across related evidence instead of analyzing every item one by one.
Do not create a separate summary merely because an item exists. Keep only themes that matter for
market context, portfolio risk, or configured focus topics.
For portfolio actions, you may synthesize and infer from all supplied summary-level evidence,
including broad-market, macro, policy-risk, sector/theme, and company-specific evidence. Company
news is not required when market or policy evidence directly affects a holding's configured themes,
but every factual premise must be supported by the evidence IDs you cite. Holdings configuration
identifies what to analyze; it is not factual evidence. When evidence is insufficient, choose
观察 or 持有 with low confidence and say why. Never infer that no event happened merely because
collectors did not capture it. Say "配置的可靠信源未采集到相关条目", never "窗口内无事件".
Do not give position sizes, price targets, or personalized advice.
Return JSON only. Do not return Markdown, URLs, citations, commentary, or extra keys.
{guidance_block}
"""

    user = f"""Run date: {run_date.isoformat()}

Return exactly this JSON shape:
{{
  "market_summaries": [
    {{
      "topic": "Market, policy, sector, or portfolio-relevant theme",
      "summary": "A concise synthesis across related supplied evidence.",
      "evidence_ids": ["EVID-001"]
    }}
  ],
  "portfolio_actions": [
    {{
      "ticker": "NVDA",
      "action": "加仓|减仓|持有|观察",
      "confidence": "低|中|高",
      "rationale": "Evidence-grounded reasoning for this action.",
      "evidence_ids": ["EVID-001"],
      "watch_for": "What reliable confirmation or risk to watch next."
    }}
  ]
}}

Requirements:
- Return at most 6 market_summaries. Fewer is better when the evidence packet is thin.
- Market summaries must aggregate related evidence; do not produce one entry per evidence item.
- Keep each market summary under 1000 characters.
- Every market summary must cite at least one supplied evidence ID.
- Omit low-value price-only routine movement from market_summaries unless it is necessary context
  for a cited portfolio action.
- Do not calculate new averages, ratios, percentage changes, ownership reduction percentages,
  or unit conversions unless that exact number already appears in the supplied evidence.
  Use qualitative wording instead, such as "规模较大" or "接近窗口高点".
- For SEC Form 4 items, translate table fields into plain Chinese when present, especially
  acquired/disposed, shares, price per share, transaction date, and post-transaction holdings.
- Return exactly one portfolio action for every configured holding and no others.
- Portfolio action must be one of 加仓, 减仓, 持有, 观察.
- Portfolio confidence must be one of 低, 中, 高.
- Every 加仓 or 减仓 action must cite at least one supplied evidence ID, and the rationale must
  explain why broad-market, policy, theme, or company evidence is directly relevant to that holding.
- If evidence_ids is empty, confidence must be 低 and the rationale must explain the evidence gap.
- Use evidence_ids to support every factual premise in rationale and watch_for.
- Do not require company-specific news for every holding. Broad-market, policy-risk, macro, and
  theme evidence may support 持有/观察 and, when directly tied to the holding's themes, may support
  加仓/减仓. Cite those evidence IDs explicitly.
- Yahoo Finance market-data snapshots may be used as price context, but price-only movement
  without company, filing, official, policy, macro, theme, or reputable-reporting evidence is not
  enough for 加仓/减仓 or for 中/高 confidence.
- Do not use title_only or metadata_only evidence for operation analysis; they are not supplied here.
- Do not state that no other events happened or that a market/watchlist is quiet.
- Preserve technical terms, names, dates, numbers, and qualifiers. When translating
  an English technical term, keep the original term in parentheses.
- Do not include URLs or unsupported numerical targets; the local renderer adds verified source links.

Configured Holdings (analysis targets, not evidence):
{holdings_json}

Verified Evidence Pack:
{evidence_markdown.strip() or "(No verified evidence was collected.)"}
"""
    return system, user
