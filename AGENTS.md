# Market Analyzer Agents

## Project Goal

Build an end-to-end portfolio tracking system for A-share and US equity
portfolios. The system must use Beijing time (`Asia/Shanghai`) for all
user-facing schedules and timestamps while respecting each market's own
calendar, session boundaries, holidays, and US daylight-saving changes.

The intended workflow is:

1. Before each market opens, collect and organize current market, company,
   portfolio, and macro news into a traceable evidence brief.
2. During each market session, retrieve current and historical price data at
   configurable high frequency, combine price changes with verified news, and
   periodically produce evidence-grounded operation suggestions.
3. After each market closes, ingest the user's actual operation records,
   compare decisions with prior suggestions and market outcomes, and produce a
   review that improves future judgment.

A-share and US equity workflows are separate market systems. They may share
domain models and infrastructure, but must not share market calendars,
session state, data freshness assumptions, or recommendation state.

## Current Implementation Boundary

The repository now contains a foundation for the full workflow: pre-market
briefs, independent A-share and US session calculation, SQLite quote and
audit storage, configurable intraday polling, conservative timed suggestions,
explicit operation ingestion, post-market review, and a transport-neutral
conversation outbox.

The built-in Yahoo Finance adapter is a reference data source, not a claim of
licensed exchange-grade real-time data. Production use still requires
official holiday feeds, selected licensed quote providers, and an approved
model-backed recommendation policy. Without verified news and an approved
judgment backend, intraday output must remain non-directional.

Intraday model judgment uses a bounded discussion protocol: market and news
analysts provide separate views, bull and bear researchers debate, a risk
manager challenges the result, and a portfolio manager emits the final
structured decision. Preserve the full transcript for audit. Do not add roles
that consume the same inputs without a distinct decision responsibility.

The future conversation channel must be behind an interface so its transport
and product design can be selected later. Do not add a chat framework until a
technical design is explicitly approved.

See `PROJECT_PLAN.md` for scope, architecture boundaries, and delivery phases.

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 — Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 — Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 — Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.
