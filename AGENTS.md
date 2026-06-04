# Daily Research Agent

You are running inside the `dailyresearch` project.

When asked to produce a daily research brief:

- Read `config/research-context.md`, `config/sources.json`, and `memory/feedback.md`.
- Prioritize primary sources, official announcements, filings, papers, project docs, and reputable reporting.
- Prefer freshness, relevance to the user's watchlist, and decision-useful detail over volume.
- Cover the Asia/Shanghai window from 00:00 of the previous calendar day through the actual run time.
- Organize the brief into A-share market/key-theme information, US market/macro information, holding-company information, and evidence-grounded US holding actions.
- Treat `portfolios.a_share.holdings` as an optional placeholder and `portfolios.us_equities.holdings` as the action-analysis portfolio.
- Report collector coverage so an empty result is distinguishable from a disabled, key-missing, reserved, or failed source.
- Save the final brief as Markdown in the output path provided by the task prompt.
- Keep each item source-backed and include source links.
- Do not use Claude or n8n. Use Codex, local scripts, OpenAI Responses API, or Codex automations.
