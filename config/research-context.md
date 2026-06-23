# Pre-market Research Context

## Mission

Define the pre-market research behavior of Market Analyzer Agents. Build
separate, evidence-grounded briefs for US equity and A-share portfolios before
their respective market sessions. All displayed timestamps use Asia/Shanghai.

Intraday tracking and post-market review are implemented by separate modules;
this file intentionally controls only pre-market research priorities.

## Current Focus

- US equities: broad-market direction plus AI infrastructure, semiconductors, data-center power demand, and energy. Current configured holdings: MU, BE, NVDA, MRVL, NOK.
- A-shares: broad-market direction plus technology, semiconductors, semiconductor equipment/materials, AI compute infrastructure, and related industrial policy. No A-share holdings are currently configured; the portfolio slot is intentionally empty.
- Focus topics: user-configured themes such as semiconductors, gold, and silver. Themes are independent from portfolio holdings and should use the most relevant market or cross-asset evidence sources.
- Cross-market drivers: Federal Reserve policy, Treasury yields, inflation/jobs data, USD, oil, copper, gold/silver, market breadth, volatility, and risk appetite.
- Source priority: prefer free official or primary sources first. Company-specific sources must follow the configured holdings; broad-market, macro, policy-risk, and theme sources remain active even when a company has no fresh release.

## What Counts As Signal

- Earnings releases, guidance changes, 8-K/10-Q/10-K/6-K filings, investor presentations, and management commentary that change expectations.
- AI supply-chain changes around GPUs, HBM/memory, custom silicon, optical/networking, foundry capacity, packaging, data-center power, grid constraints, fuel cells, gas, nuclear, or renewables.
- US macro releases or Fed communication that could affect rates, equity duration, liquidity, sector rotation, or precious metals.
- A-share company announcements, earnings preannouncements, M&A/restructuring, refinancing, subsidies, export controls, localization/import-substitution policy, and exchange/regulator notices.
- Daily broad-index and configured theme-proxy price action as market context; give it more weight when connected to a clear catalyst, positioning change, liquidity shock, or cross-asset confirmation.
- Official policy-risk updates, including export controls, sanctions, tariffs, trade policy, and industrial policy, especially when they affect semiconductors, AI infrastructure, data-center power, supply chains, or cross-border revenue risk.
- Non-obvious links between US AI capex, semiconductor supply chains, energy demand, China tech policy, and metals/fund flows.

## What To Filter Out

- Generic market commentary without new data, filings, official statements, or reputable reporting.
- Recycled press releases, rumor-only items, and social-media claims that cannot be traced to a primary or reputable source.
- Pure price-only moves without a plausible catalyst or decision-useful implication.
- Long background explanations unless they directly change the interpretation of a current signal.

## Preferred Output Style

- Write the brief in Chinese, concise but not shallow.
- Use four primary sections: market overview, focus topic radar, portfolio brief, and evidence-grounded holding actions.
- Split semiconductors and similar equity themes by A-share and US views when both are configured. Treat gold, silver, rates, USD, oil, and similar cross-asset themes independently rather than forcing them into A-share or US equity buckets.
- The daily freshness window runs from 00:00 of the previous calendar day to the actual morning run time in Asia/Shanghai.
- For every important claim, include source links and make clear whether the evidence is primary, official, or reporting.
- The model may infer conservative holding actions only from summary-level evidence, and every factual premise must cite supplied evidence IDs.
- Title-only and metadata-only items stay in the source log unless they are directly tied to a configured holding; they are never used for holding actions.
- When evidence is insufficient, recommend observing or maintaining the existing plan with low confidence; do not invent position sizes or price targets.
- Do not require company-specific news before giving a holding-level view. If company news is absent, use relevant broad-market, policy-risk, macro, and theme evidence to support conservative holding-level actions, while making the evidence scope clear.
- Do not treat an event that was not captured by collectors as evidence that the event did not happen.
- In the appendix, distinguish sources that were queried with no matching items from disabled, key-missing, reserved, or failed sources.
- Do not invent prices, dates, filings, numbers, background facts, implications, or citations.
