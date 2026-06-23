# Market Analyzer Agents - Project Plan

## 1. Product Objective

Provide one program that follows a user's A-share and US equity portfolios
through the full daily investment workflow:

- pre-market intelligence collection and briefing;
- intraday quote tracking, context updates, and timed operation suggestions;
- post-market ingestion of actual operations and evidence-based review.

All user-facing times and schedules use Beijing time (`Asia/Shanghai`).
Market logic must still follow the official calendar and session rules of the
relevant exchange. US sessions must be converted from `America/New_York`
dynamically so daylight-saving changes are never encoded as fixed Beijing
hours.

The product provides research support, not guaranteed returns or automated
trade execution. Every suggestion must expose its evidence, timestamp, data
freshness, confidence, and invalidation conditions.

## 2. Market Separation

The system has two independent market workflows:

| Concern | A-share system | US equity system |
| --- | --- | --- |
| Portfolio | A-share holdings and funds | US stocks and funds |
| Calendar | Chinese exchange calendar | US exchange calendar |
| Sessions | Auction, morning, midday break, afternoon | Pre-market if enabled, regular session, after-hours if enabled |
| Disclosures | Chinese exchanges and statutory platforms | SEC and issuer filings |
| Quotes | A-share data provider | US market data provider |
| State | Independent freshness and signal state | Independent freshness and signal state |

Shared components may include normalized portfolio records, evidence models,
storage, audit logs, rendering, and model adapters. Cross-market information
may be referenced as evidence, but one market's session state must never drive
the other market's scheduler.

## 3. Daily Workflow

### Pre-market

Run separately before each market opens:

1. Load the relevant portfolio snapshot.
2. Collect current company disclosures, reputable news, macro events, and
   broad-market/theme data.
3. Normalize, deduplicate, grade, and timestamp evidence.
4. Produce a market-specific brief with portfolio impact and items to watch.

### Intraday

Run only while the relevant market session is active:

1. Poll current quotes at a configurable interval permitted by the provider.
2. Persist normalized price points and obtain the required historical window.
3. Detect deterministic changes such as return, volume, volatility, gaps, and
   threshold crossings in code.
4. Attach newly verified news and disclosures.
5. At configured review intervals or material events, ask the model for a
   judgment using only supplied portfolio, price features, and evidence.
6. Emit a suggestion with action, confidence, rationale, evidence IDs,
   freshness, and invalidation conditions.

Models must not perform routing, retries, market-session calculation, numeric
feature calculation, or threshold detection.

### Post-market

Run separately after each market closes:

1. Import user-confirmed trades and non-trade decisions through a stable
   operation-ingestion interface.
2. Reconstruct what information and suggestions were available at decision
   time.
3. Compare intended action, actual action, and subsequent market movement.
4. Separate process quality from outcome quality to avoid hindsight bias.
5. Save review findings as structured feedback for future briefs and
   suggestions.

## 4. Core Domain Boundaries

- `MarketCalendar`: exchange days and session state, rendered in Beijing time.
- `Portfolio`: holdings, cash metadata, and portfolio snapshots by market.
- `MarketData`: current quotes, historical bars, provider provenance, and
  freshness.
- `Evidence`: news and disclosure items with source quality and traceability.
- `Signal`: deterministic price/news triggers.
- `Suggestion`: model judgment with evidence and audit metadata.
- `OperationRecord`: user-confirmed action; never inferred from a suggestion.
- `Review`: post-market comparison and reusable feedback.
- `ConversationPort`: transport-neutral delivery/input interface reserved for
  a later technical design.

Automated order placement is outside the current scope.

## 5. Delivery Phases

### Phase 0 - Pre-market foundation

Implemented:

- independent collectors and evidence grading;
- A-share and US portfolio configuration;
- market/theme snapshots and source logs;
- model-backed brief generation;
- daily scheduler and Markdown output.

### Implemented workflow foundation

- independent A-share and US regular-session calculation in Beijing time;
- SQLite quote, suggestion, and operation audit storage;
- configurable A-share and US intraday polling with Yahoo reference quotes,
  historical OHLCV backfill, volatility metrics, and SQLite persistence;
- deterministic freshness/price context and conservative suggestions;
- timed multi-agent discussion with market/news analysts, bull/bear
  researchers, risk review, and portfolio-manager adjudication;
- explicit operation ingestion and post-market review;
- transport-neutral conversation interface with a JSONL outbox adapter.

Production deployment still requires official holiday data, selected licensed
quote providers, and an approved model-backed recommendation policy.

### Phase 1 - Portfolio and market foundation (foundation implemented)

- define normalized market, portfolio, operation, and timestamp schemas;
- add official market-calendar/session abstraction;
- separate A-share and US runtime configuration;
- add durable local storage and migrations;
- keep the existing pre-market command behavior compatible.

Exit criterion: both markets can independently determine their current session
in Beijing time and load a versioned portfolio snapshot.

### Phase 2 - Intraday market data (reference implementation available)

- select licensed/reliable quote providers for each market;
- ingest current and historical bars with retry, rate-limit, and stale-data
  handling;
- calculate price features deterministically;
- persist observations and expose replayable test fixtures.

Exit criterion: a full market day can be replayed without network access and
produces the same normalized observations and signals.

### Phase 3 - Timed suggestions (foundation implemented)

- combine deterministic signals with newly verified evidence;
- add configurable review intervals and material-event triggers;
- produce auditable, portfolio-aware suggestions;
- enforce stale-data, missing-evidence, and market-closed safeguards.

Exit criterion: every suggestion can be reproduced from stored inputs and
contains no unsupported factual premise.

### Phase 4 - Operations and post-market review (foundation implemented)

- implement manual/imported operation ingestion;
- connect operations to the suggestion and evidence available at decision
  time;
- generate separate A-share and US reviews;
- feed structured lessons into later runs without treating outcomes as proof
  that a decision process was correct or incorrect.

Exit criterion: a user can reconcile a complete day from pre-market brief
through intraday suggestions to post-market review.

### Phase 5 - Conversation integration

- keep the current JSONL outbox as the only built-in transport;
- approve a delivery/input technical design before adding any chat vendor;
- add authentication, user isolation, acknowledgement, and delivery retries
  only after that design is approved.

Exit criterion: changing conversation providers does not alter portfolio,
market-data, suggestion, or review logic.

## 6. Near-term Priorities

1. Replace manually configured holidays with authoritative exchange calendars.
2. Add schema migrations, replay fixtures, and provider retry/rate-limit
   policies.
3. Evaluate A-share and US quote providers against licensing, latency,
   historical coverage, rate limits, and cost.
4. Approve the production recommendation policy and conversation-channel design.

## 7. Non-goals Until Explicitly Approved

- automated brokerage execution;
- guaranteed-return language or unqualified personalized financial advice;
- fixed US Beijing trading hours that ignore daylight saving;
- one shared scheduler/state machine for both markets;
- model-generated numeric indicators that code can calculate;
- a chat-vendor dependency inside core portfolio logic.
