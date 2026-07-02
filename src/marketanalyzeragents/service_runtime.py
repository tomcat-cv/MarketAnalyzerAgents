from __future__ import annotations

import argparse
import json
import sys
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .brief_workflow import run_brief_command, store_and_outbox
from .collectors import collect_evidence
from .config import load_market_settings, load_settings, read_inputs, resolve_path
from .env import load_dotenv
from .evidence import EvidencePack, configured_portfolio_holdings
from .intraday_workflow import run_intraday_command
from .market_calendar import calendar_from_settings, market_status
from .scheduler import ScheduleError, local_now, next_run_at
from .writer import write_json


def configured_brief_markets(settings: dict[str, Any], requested_market: str | None = None) -> list[str]:
    if requested_market:
        return [requested_market]
    paths = settings.get("market_config_paths", {})
    if isinstance(paths, dict):
        markets = [market for market in ("a_share", "us_equities") if market in paths]
        if markets:
            return markets
    return []


def configured_intraday_markets(
    inputs: dict[str, Any],
    requested_markets: Sequence[str] | None,
) -> list[str]:
    if requested_markets:
        return list(dict.fromkeys(requested_markets))
    markets = [
        holding["market"]
        for holding in configured_portfolio_holdings(inputs.get("sources", {}))
        if holding.get("market") in {"a_share", "us_equities"}
    ]
    return list(dict.fromkeys(markets))


def collect_and_store_evidence(root: Path, settings: dict[str, Any]) -> EvidencePack:
    inputs = read_inputs(root, settings)
    pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
    store, _ = store_and_outbox(root, settings)
    try:
        store.save_evidence_pack(pack.to_dict())
    finally:
        store.close()
    return pack


def write_service_health(
    root: Path,
    settings: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    service_settings = settings.get("service", {})
    path = resolve_path(root, service_settings.get("health_path", "state/service-health.json"))
    write_json(path, payload)


def _run_brief_for_market(args: Any, market: str) -> int:
    market_args = argparse.Namespace(**vars(args))
    market_args.market = market
    return run_brief_command(market_args)


def run_service_command(args: Any, root: Path) -> int:
    load_dotenv(root / ".env")
    settings = load_settings(root)
    inputs = read_inputs(root, settings)
    intraday_markets = configured_intraday_markets(inputs, args.markets)
    brief_markets = [] if args.no_briefs else (args.markets or configured_brief_markets(settings))
    if not intraday_markets and not brief_markets and args.no_news_watch:
        print(
            "No configured markets and news watch is disabled. Add holdings, pass --markets, "
            "configure market schedules, or enable news watch.",
            file=sys.stderr,
        )
        return 2

    next_brief_targets: dict[str, datetime] = {}
    last_intraday_poll: dict[str, float] = {}
    next_intraday_allowed: dict[str, float] = {}
    failure_backoff: dict[str, float] = {}
    market_health: dict[str, dict[str, Any]] = {market: {"state": "starting"} for market in intraday_markets}
    news_health: dict[str, Any] = {"state": "starting"}
    last_news_collect = 0.0
    next_news_allowed = 0.0
    news_failure_backoff = 0.0
    last_retention_day = None
    print(
        "Service started. "
        f"Brief schedule enabled={not args.no_briefs}; "
        f"news watch enabled={not args.no_news_watch}; "
        f"brief markets={', '.join(brief_markets) if brief_markets else 'none'}; "
        f"intraday markets={', '.join(intraday_markets) if intraday_markets else 'none'}.",
        flush=True,
    )

    while True:
        try:
            settings = load_settings(root)
            now = local_now(str(settings.get("timezone", "Asia/Shanghai")))
            service_settings = settings.get("service", {})
            today = now.date().isoformat()
            if last_retention_day != today:
                store, _ = store_and_outbox(root, settings)
                try:
                    pruned = store.prune_market_data(
                        int(service_settings.get("retention_days", 120)),
                    )
                finally:
                    store.close()
                last_retention_day = today
                print(f"Retention cleanup complete: {json.dumps(pruned, ensure_ascii=False)}", flush=True)
            if not args.no_briefs:
                brief_markets = args.markets or configured_brief_markets(settings)
                for market in brief_markets:
                    market_settings = load_market_settings(root, settings, market)
                    next_target = next_brief_targets.get(market)
                    if next_target is None or next_target <= now:
                        if next_target is not None:
                            exit_code = _run_brief_for_market(args, market)
                            if exit_code != 0:
                                print(
                                    f"Scheduled brief failed for {market} with exit code {exit_code}.",
                                    file=sys.stderr,
                                )
                        next_brief_targets[market] = next_run_at(now, market_settings)
                        print(
                            f"Next scheduled brief for {market}: {next_brief_targets[market].isoformat(timespec='seconds')}",
                            flush=True,
                        )

            if not args.no_news_watch:
                news_settings = settings.get("news_collection", {})
                interval = int(args.news_interval or news_settings.get("interval_seconds", 900))
                now_monotonic = time_module.monotonic()
                if now_monotonic >= next_news_allowed and now_monotonic - last_news_collect >= interval:
                    try:
                        pack = collect_and_store_evidence(root, settings)
                        last_news_collect = now_monotonic
                        next_news_allowed = 0.0
                        news_failure_backoff = 0.0
                        news_health = {
                            "state": "collect_ok",
                            "items": len(pack.items),
                            "errors": len(pack.errors),
                            "retrieved_at": pack.retrieved_at,
                            "as_of_beijing": now.isoformat(),
                        }
                        print(
                            f"News collection stored {len(pack.items)} items; warnings={len(pack.errors)}.",
                            flush=True,
                        )
                    except Exception as exc:
                        previous_backoff = news_failure_backoff or float(args.tick_seconds)
                        news_failure_backoff = min(
                            float(service_settings.get("max_backoff_seconds", 300)),
                            max(float(args.tick_seconds), previous_backoff * 2),
                        )
                        next_news_allowed = time_module.monotonic() + news_failure_backoff
                        news_health = {
                            "state": "collect_failed",
                            "error": str(exc),
                            "backoff_seconds": news_failure_backoff,
                            "as_of_beijing": now.isoformat(),
                        }
                        print(f"News collection failed: {exc}", file=sys.stderr)

            for market in intraday_markets:
                market_settings = load_market_settings(root, settings, market).get("markets", {}).get(market, {})
                status = market_status(
                    market,
                    holidays=market_settings.get("holidays", []),
                    extra_open_dates=market_settings.get("extra_open_dates", []),
                    early_closes=market_settings.get("early_closes", {}),
                    calendar=calendar_from_settings(market_settings, root=root),
                )
                if status.state != "open":
                    market_health[market] = {
                        "state": status.state,
                        "as_of_beijing": status.as_of_beijing.isoformat(),
                    }
                    continue
                interval = args.interval or int(market_settings.get("poll_interval_seconds", 60))
                now_monotonic = time_module.monotonic()
                allowed_at = next_intraday_allowed.get(market, 0.0)
                if now_monotonic < allowed_at:
                    continue
                previous = last_intraday_poll.get(market)
                if previous is not None and now_monotonic - previous < interval:
                    continue
                last_intraday_poll[market] = now_monotonic
                intraday_args = argparse.Namespace(
                    market=market,
                    watch=False,
                    interval=interval,
                    force=False,
                    with_news=args.with_news,
                    advice_backend=args.advice_backend,
                    debate_rounds=args.debate_rounds,
                    emit_low_signal=args.emit_low_signal,
                )
                exit_code = run_intraday_command(intraday_args, root)
                if exit_code != 0:
                    previous_backoff = failure_backoff.get(market, float(args.tick_seconds))
                    backoff = min(
                        float(service_settings.get("max_backoff_seconds", 300)),
                        max(float(args.tick_seconds), previous_backoff * 2),
                    )
                    failure_backoff[market] = backoff
                    next_intraday_allowed[market] = time_module.monotonic() + backoff
                    market_health[market] = {
                        "state": "poll_failed",
                        "exit_code": exit_code,
                        "backoff_seconds": backoff,
                        "as_of_beijing": now.isoformat(),
                    }
                    print(
                        f"Intraday poll failed for {market} with exit code {exit_code}.",
                        file=sys.stderr,
                    )
                else:
                    failure_backoff.pop(market, None)
                    next_intraday_allowed.pop(market, None)
                    market_health[market] = {
                        "state": "poll_ok",
                        "as_of_beijing": now.isoformat(),
                    }
            write_service_health(
                root,
                settings,
                {
                    "status": "running",
                    "updated_at": local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat(),
                    "brief_schedule_enabled": not args.no_briefs,
                    "news_watch_enabled": not args.no_news_watch,
                    "next_brief_targets": {
                        market: target.isoformat() for market, target in next_brief_targets.items()
                    },
                    "news": news_health,
                    "markets": market_health,
                },
            )
            time_module.sleep(args.tick_seconds)
        except KeyboardInterrupt:
            write_service_health(
                root,
                settings,
                {
                    "status": "stopped",
                    "updated_at": local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat(),
                    "markets": market_health,
                },
            )
            print("Service stopped.")
            return 130
        except ScheduleError as exc:
            print(f"Invalid schedule configuration: {exc}", file=sys.stderr)
            return 2
