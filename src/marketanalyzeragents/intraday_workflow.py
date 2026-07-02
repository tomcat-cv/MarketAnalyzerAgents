from __future__ import annotations

import json
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .agent_debate import backend_invoker, run_agent_debate
from .collectors import HttpClient, collect_evidence
from .config import load_market_settings, load_settings, read_inputs, resolve_path
from .env import load_dotenv
from .evidence import configured_portfolio_holdings
from .intraday import (
    MarketDataProviderError,
    build_outbox,
    build_suggestion,
    fetch_market_data,
    market_history_payload,
    should_emit_suggestion,
    should_run_agent_debate,
)
from .market_calendar import calendar_from_settings, market_status
from .portfolio_store import PortfolioStore


def store_and_outbox(root: Path, settings: dict[str, Any]) -> tuple[PortfolioStore, Any]:
    state = settings.get("state", {})
    store = PortfolioStore(resolve_path(root, state.get("database_path", "state/portfolio.db")))
    outbox = build_outbox(root, settings)
    return store, outbox


def stored_evidence_by_ticker(
    store: PortfolioStore,
    holdings: Sequence[dict[str, Any]],
    *,
    max_items_per_symbol: int,
    lookback_hours: int,
) -> dict[str, list[dict[str, Any]]]:
    tickers = [str(holding.get("ticker", "")).upper().strip() for holding in holdings]
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    grouped = store.recent_summary_evidence_for_tickers(tickers, since=since)
    return {
        ticker: items[:max_items_per_symbol]
        for ticker, items in grouped.items()
        if items
    }


def run_intraday_command(args: Any, root: Path) -> int:
    load_dotenv(root / ".env")
    settings = load_market_settings(root, load_settings(root), args.market)
    inputs = read_inputs(root, settings)
    holdings = [
        holding for holding in configured_portfolio_holdings(inputs.get("sources", {}))
        if holding["market"] == args.market
    ]
    if not holdings:
        print(f"No configured holdings for {args.market}.", file=sys.stderr)
        return 2
    store, outbox = store_and_outbox(root, settings)
    market_settings = settings.get("markets", {}).get(args.market, {})
    agent_settings = settings.get("intraday_agents", {})
    interval = args.interval or int(market_settings.get("poll_interval_seconds", 60))
    debate_rounds = args.debate_rounds or int(agent_settings.get("debate_rounds", 1))
    advice_backend = getattr(args, "advice_backend", None) or str(
        agent_settings.get("advice_backend", "conservative")
    )
    if advice_backend not in {"conservative", "zhipu", "openai"}:
        print(
            "Invalid intraday_agents.advice_backend; expected conservative, zhipu, or openai.",
            file=sys.stderr,
        )
        return 2
    max_agent_evidence = int(agent_settings.get("max_evidence_items_per_symbol", 8))
    history_points = int(agent_settings.get("price_history_points", 20))
    market_data_settings = settings.get("market_data", {})
    client = HttpClient(
        str(settings.get("collectors", {}).get("user_agent", "market-analyzer-agents/0.1")),
        int(settings.get("collectors", {}).get("timeout_seconds", 30)),
    )
    while True:
        status = market_status(
            args.market,
            holidays=market_settings.get("holidays", []),
            extra_open_dates=market_settings.get("extra_open_dates", []),
            early_closes=market_settings.get("early_closes", {}),
            calendar=calendar_from_settings(market_settings, root=root),
        )
        if status.state != "open" and not args.force:
            print(f"{args.market} is {status.state}; no intraday polling performed.")
            return 0
        evidence_by_ticker = stored_evidence_by_ticker(
            store,
            holdings,
            max_items_per_symbol=max_agent_evidence,
            lookback_hours=int(settings.get("lookback_hours", 24)),
        )
        if args.with_news:
            pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
            store.save_evidence_pack(pack.to_dict())
            for item in pack.items:
                if item.evidence_level != "summary":
                    continue
                for ticker in item.matched_tickers:
                    evidence_by_ticker.setdefault(ticker.upper(), []).insert(0, item.__dict__)
        failures = []
        for holding in holdings:
            try:
                data = fetch_market_data(
                    client,
                    args.market,
                    holding["symbol"],
                    market_data_settings,
                )
            except MarketDataProviderError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            except Exception as exc:
                failures.append((holding["symbol"], exc))
                print(
                    f"Intraday market data failed for {args.market} {holding['symbol']}: {exc}",
                    file=sys.stderr,
                )
                continue
            quote = data.quote
            store.save_quotes([quote])
            store.save_price_bars(data.history)
            evidence = evidence_by_ticker.get(holding["ticker"].upper(), [])[:max_agent_evidence]
            turns = []
            suggestion = build_suggestion(
                store, quote, [item["id"] for item in evidence]
            )
            if advice_backend != "conservative" and should_run_agent_debate(suggestion):
                debate = run_agent_debate(
                    quote=quote,
                    evidence=evidence,
                    invoke=backend_invoker(settings, advice_backend),
                    rounds=debate_rounds,
                    price_history=market_history_payload(data, history_points),
                    portfolio=holding,
                )
                suggestion = {
                    **debate.suggestion,
                    "price_change_pct": suggestion.get("price_change_pct"),
                    "signal": suggestion.get("signal"),
                    "decision_source": "agent_debate",
                }
                turns = debate.turns
            if not should_emit_suggestion(
                suggestion,
                emit_low_signal=bool(getattr(args, "emit_low_signal", False)),
            ):
                continue
            suggestion["id"] = store.save_suggestion(suggestion)
            if turns:
                store.save_discussion(suggestion["id"], turns)
                outbox.deliver(
                    {
                        "type": "intraday_agent_discussion",
                        "suggestion_id": suggestion["id"],
                        "market": quote.market,
                        "symbol": quote.symbol,
                        "turns": [turn.__dict__ for turn in turns],
                    }
                )
            outbox.deliver(suggestion)
            print(json.dumps(suggestion, ensure_ascii=False))
        if len(failures) == len(holdings):
            print(f"Intraday polling failed for all {args.market} holdings.", file=sys.stderr)
            return 1
        if not args.watch:
            return 0
        time_module.sleep(interval)
