from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .evidence import configured_portfolio_holdings


def configured_guidance(settings: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    sections = []
    if str(inputs.get("context", "")).strip():
        sections.append(("Research context and output preferences", str(inputs["context"]).strip()))
    if str(inputs.get("feedback", "")).strip():
        sections.append(("Prior feedback", str(inputs["feedback"]).strip()))
    if str(inputs.get("prompt_extra", "")).strip():
        sections.append(("Additional configured prompt instructions", str(inputs["prompt_extra"]).strip()))
    if not sections:
        return ""

    max_chars = int(settings.get("prompt", {}).get("max_extra_instruction_chars", 6000))
    content = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    return content[:max_chars].strip()


def build_openai_messages(
    *,
    settings: Mapping[str, Any],
    inputs: Mapping[str, Any],
    run_date: date,
    output_path: Path,
    output_format: str = "markdown",
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
1. Compress each supplied evidence item into a factual summary.
2. Add one reader-friendly analysis note for each supplied evidence item.
3. Give one conservative action assessment for every configured holding.

For summaries, use only facts explicitly present in that item's Evidence field, title,
and verified Published timestamp.
For per-item analysis, explain what the item means for a reader, what matters, and what
is still uncertain. If the evidence is an SEC Form 4, explicitly explain the reporting
person, transaction direction, share count, price, and post-transaction ownership when
those fields are present in the evidence.
For portfolio actions, you may synthesize and infer from the supplied summary-level evidence,
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
  "summaries": [
    {{
      "evidence_id": "EVID-001",
      "summary": "One or two concise sentences that directly paraphrase only this evidence.",
      "analysis": "One reader-friendly interpretation of why this item matters, and what remains uncertain."
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
- Return exactly one entry for every evidence item, using its exact evidence ID.
- Keep each summary under 800 characters.
- Keep each analysis under 1000 characters.
- Summary entries must not add facts, implications, market interpretation, background, or next steps
  beyond the supplied title, Evidence field, and verified Published timestamp.
- Analysis entries may explain implications and caveats. If comparing with another supplied
  evidence item, name its evidence ID. Do not invent missing table fields or unstated causes.
- Do not calculate new averages, ratios, percentage changes, ownership reduction percentages,
  or unit conversions unless that exact number already appears in the supplied evidence.
  Use qualitative wording instead, such as "规模较大" or "接近窗口高点".
- For SEC Form 4 items, translate table fields into plain Chinese when present, especially
  acquired/disposed, shares, price per share, transaction date, and post-transaction holdings.
- Return exactly one portfolio action for every configured holding and no others.
- Portfolio action must be one of 加仓, 减仓, 持有, 观察.
- Portfolio confidence must be one of 低, 中, 高.
- Every 加仓 or 减仓 action must cite at least one supplied evidence ID.
- If evidence_ids is empty, confidence must be 低 and the rationale must explain the evidence gap.
- Use evidence_ids to support every factual premise in rationale and watch_for.
- Yahoo Finance market-data snapshots may be used as price context, but price-only movement
  without company, filing, official, macro, or reputable-reporting evidence is not enough for
  加仓/减仓 or for 中/高 confidence.
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


def build_codex_task_prompt(
    *,
    settings: Mapping[str, Any],
    inputs: Mapping[str, Any],
    run_date: date,
    output_path: Path,
    output_format: str = "markdown",
    evidence_markdown: str = "",
) -> str:
    holdings = configured_portfolio_holdings(inputs.get("sources", {}))
    holdings_json = json.dumps(holdings, ensure_ascii=False, indent=2)
    guidance = configured_guidance(settings, inputs)
    guidance_block = (
        "\nConfigured guidance (style/priorities only; not factual evidence):\n"
        f"{guidance}\n"
        if guidance
        else ""
    )
    return f"""You are Codex running the Daily Research Agent in this repository.

Goal: create today's daily research brief and save it to:
{output_path}

Hard constraints:
- Do not use Claude.
- Do not use n8n.
- Use Codex tools plus local files in this repo.
- Do not use web research/search. Use only the Verified Evidence Pack below.
- Do not make network requests. The independent collectors already completed retrieval.
- Summarize only facts explicitly present in each Evidence field and title.
- For each summary-level item, include a reader-friendly interpretation that explains why it matters
  and what remains uncertain. For SEC Form 4, translate the table fields into plain Chinese when present.
- You may analyze portfolio actions only from summary-level evidence. Cite the supporting
  evidence IDs and source links for every action rationale.
- Do not use title_only or metadata_only items for portfolio action analysis; display them
  as pending verification only.
- For every configured holding, choose exactly one of 加仓, 减仓, 持有, 观察 and state confidence.
- When evidence is insufficient, prefer 观察 or 持有 with low confidence. Do not infer that
  no event happened because collectors did not capture it.
- Treat Yahoo Finance market-data snapshots as price context; price-only movement without
  company, filing, official, macro, or reputable-reporting evidence is not enough for 加仓/减仓
  or for 中/高 confidence.
- Do not calculate new averages, ratios, percentage changes, ownership reduction percentages,
  or unit conversions unless that exact number already appears in the supplied evidence.
- Do not give position sizes, price targets, or personalized advice.
- Include each evidence item's exact source link.
- Write the final brief in {settings.get("language", "zh-CN")}.
- Output format: {output_format}. If html, write a standalone HTML file with polished
  readable styling and source links.
- Create parent directories if needed.
{guidance_block}

Run metadata:
- Date: {run_date.isoformat()}
- Timezone: {settings.get("timezone", "Asia/Shanghai")}
- Freshness window: previous calendar day 00:00 through the actual run time in the configured timezone.
- Max items: {settings.get("max_items", 12)}

Configured Holdings (analysis targets, not evidence):
{holdings_json}

Verified Evidence Pack:
{evidence_markdown.strip() or "(No verified evidence was collected.)"}

Content structure:
# 每日研究简报 - {run_date.isoformat()}

## 1. 市场概览
- Include broad A-share and US market items with source and publication time.

## 2. 重点主题雷达
- Group configured focus topics separately. If a theme has A-share and US views, keep them separate.
- For cross-asset themes such as gold or silver, do not force them into A-share or US equity buckets.

## 3. 持仓简报
- Group configured holdings by market, including funds or stocks as configured.

## 4. 根据市场动态分析持仓应该作何操作
- One row per holding: action, confidence, evidence-grounded rationale, evidence links, next watch item.

Do not include an appendix with collector coverage or source logs in the brief body; the CLI writes
that log separately.

After writing the file, reply with the output path only.
"""
