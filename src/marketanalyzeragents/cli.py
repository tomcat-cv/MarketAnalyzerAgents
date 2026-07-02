from __future__ import annotations

import argparse
import json
import sys
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .brief_workflow import (
    brief_delivery_path as _brief_delivery_path,
    market_filtered_inputs as _market_filtered_inputs,
    run_brief_command,
    settings_for_market as _settings_for_market,
    store_and_outbox as _store_and_outbox,
    write_brief_outputs as _write_brief_outputs,
)
from .collectors import collect_evidence
from .config import (
    find_project_root,
    load_market_settings,
    load_settings,
    read_inputs,
    resolve_path,
)
from .env import load_dotenv
from .intraday_workflow import run_intraday_command
from .market_calendar import calendar_from_settings, market_status
from .scheduler import ScheduleError, local_now, next_run_at, seconds_until
from .service_runtime import (
    configured_brief_markets as _configured_brief_markets,
    configured_intraday_markets as _configured_intraday_markets,
    run_service_command,
)
from .review import build_daily_review
from .web import run_web_server
from .writer import run_stamp, write_json


def add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=["zhipu", "openai", "dry-run"],
        help="Execution backend. Defaults to config/settings.json.",
    )
    parser.add_argument("--date", help="Run date in YYYY-MM-DD format.")
    parser.add_argument("--output", help="Override output brief path.")
    parser.add_argument("--model", help="Override backend model for this run.")
    parser.add_argument("--market", choices=["a_share", "us_equities"], help="Generate a market-specific brief.")


def command_run(args: argparse.Namespace) -> int:
    return run_brief_command(args)


def command_collect(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    inputs = read_inputs(root, settings)
    pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
    store, _ = _store_and_outbox(root, settings)
    try:
        store.save_evidence_pack(pack.to_dict())
    finally:
        store.close()
    output_path = Path(args.output).expanduser() if args.output else root / "runs" / f"{run_stamp()}-evidence.json"
    if not output_path.is_absolute():
        output_path = root / output_path
    write_json(output_path, pack.to_dict())
    print(f"Evidence written: {output_path}")
    print("Evidence stored in SQLite.")
    print(f"Verified items: {len(pack.items)}")
    print(f"Collection warnings: {len(pack.errors)}")
    print(f"Collector coverage entries: {len(pack.coverage)}")
    return 0 if pack.items else 2


def command_market_status(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_market_settings(root, load_settings(root), args.market)
    market_settings = settings.get("markets", {}).get(args.market, {})
    status = market_status(
        args.market,
        holidays=market_settings.get("holidays", []),
        extra_open_dates=market_settings.get("extra_open_dates", []),
        early_closes=market_settings.get("early_closes", {}),
        calendar=calendar_from_settings(market_settings, root=root),
    )
    print(json.dumps(status.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0


def command_intraday(args: argparse.Namespace) -> int:
    root = find_project_root()
    return run_intraday_command(args, root)


def command_operation(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_settings(root)
    store, _ = _store_and_outbox(root, settings)
    operated_at = args.at or local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat()
    operation = {
        "market": args.market,
        "symbol": args.symbol,
        "operated_at": operated_at,
        "action": args.action,
        "quantity": args.quantity,
        "price": args.price,
        "note": args.note or "",
    }
    operation["id"] = store.save_operation(operation)
    print(json.dumps(operation, ensure_ascii=False, indent=2))
    return 0


def command_review(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_settings(root)
    store, outbox = _store_and_outbox(root, settings)
    day = args.date or local_now(str(settings.get("timezone", "Asia/Shanghai"))).date().isoformat()
    review = build_daily_review(store, args.market, day)
    output = resolve_path(root, args.output or f"reviews/{day}-{args.market}.json")
    write_json(output, review)
    outbox.deliver({"type": "post_market_review", **review, "output": str(output)})
    print(f"Review written: {output}")
    return 0


def command_web(args: argparse.Namespace) -> int:
    run_web_server(host=args.host, port=args.port, root=find_project_root())
    return 0


def _run_once_from_schedule(args: argparse.Namespace) -> int:
    run_args = argparse.Namespace(
        backend=args.backend,
        date=args.date,
        output=args.output,
        model=args.model,
        market=getattr(args, "market", None),
    )
    return command_run(run_args)


def _run_once_for_market(args: argparse.Namespace, market: str) -> int:
    market_args = argparse.Namespace(**vars(args))
    market_args.market = market
    return _run_once_from_schedule(market_args)


def command_schedule(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    brief_markets = _configured_brief_markets(settings, getattr(args, "market", None))
    if args.once:
        if not brief_markets:
            return _run_once_from_schedule(args)
        exit_code = 0
        for market in brief_markets:
            exit_code = max(exit_code, _run_once_for_market(args, market))
        return exit_code

    if not brief_markets:
        brief_markets = [None]  # type: ignore[list-item]

    for market in brief_markets:
        market_settings = _settings_for_market(root, settings, market)
        schedule = market_settings.get("schedule", {})
        if args.run_on_start or bool(schedule.get("run_on_start", False)):
            exit_code = _run_once_for_market(args, market) if market else _run_once_from_schedule(args)
            if exit_code != 0:
                print(f"Scheduled startup run failed with exit code {exit_code}.", file=sys.stderr)

    print("Scheduler started. Press Ctrl+C to stop.", flush=True)
    next_targets: dict[str, datetime] = {}
    while True:
        try:
            settings = load_settings(root)
            now = local_now(str(settings.get("timezone", "Asia/Shanghai")))
            current_markets = _configured_brief_markets(settings, getattr(args, "market", None)) or [None]  # type: ignore[list-item]
            for market in current_markets:
                key = market or "default"
                market_settings = _settings_for_market(root, settings, market)
                if key not in next_targets:
                    next_targets[key] = next_run_at(now, market_settings)
                    label = market or "default"
                    print(
                        f"Next scheduled run for {label}: {next_targets[key].isoformat(timespec='seconds')}",
                        flush=True,
                    )
                if next_targets[key] <= now:
                    exit_code = _run_once_for_market(args, market) if market else _run_once_from_schedule(args)
                    if exit_code != 0:
                        print(f"Scheduled run failed with exit code {exit_code}.", file=sys.stderr)
                    next_targets[key] = next_run_at(now, market_settings)
                    label = market or "default"
                    print(
                        f"Next scheduled run for {label}: {next_targets[key].isoformat(timespec='seconds')}",
                        flush=True,
                    )
            target = min(next_targets.values())
            wait_seconds = min(float(args.tick_seconds), seconds_until(target, now)) if hasattr(args, "tick_seconds") else seconds_until(target, now)
        except ScheduleError as exc:
            print(f"Invalid schedule configuration: {exc}", file=sys.stderr)
            return 2

        try:
            time_module.sleep(wait_seconds)
        except KeyboardInterrupt:
            print("Scheduler stopped.")
            return 130


def command_service(args: argparse.Namespace) -> int:
    root = find_project_root()
    return run_service_command(args, root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketanalyzeragents",
        description="Run Market Analyzer Agents workflows.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Generate a pre-market research brief.")
    add_common_run_args(run_parser)
    run_parser.set_defaults(func=command_run)

    collect_parser = subparsers.add_parser("collect", help="Collect and save a verified Evidence Pack.")
    collect_parser.add_argument("--output", help="Output Evidence Pack JSON path.")
    collect_parser.set_defaults(func=command_collect)

    status_parser = subparsers.add_parser("market-status", help="Show a market session in Beijing time.")
    status_parser.add_argument("--market", choices=["a_share", "us_equities"], required=True)
    status_parser.set_defaults(func=command_market_status)

    intraday_parser = subparsers.add_parser("intraday", help="Poll quotes and emit auditable suggestions.")
    intraday_parser.add_argument("--market", choices=["a_share", "us_equities"], required=True)
    intraday_parser.add_argument("--watch", action="store_true", help="Continue polling while the market is open.")
    intraday_parser.add_argument("--interval", type=int, help="Polling interval in seconds.")
    intraday_parser.add_argument("--force", action="store_true", help="Run once even when the market is closed.")
    intraday_parser.add_argument(
        "--with-news", action="store_true", help="Collect verified news before each advice cycle."
    )
    intraday_parser.add_argument(
        "--advice-backend",
        choices=["conservative", "zhipu", "openai"],
        help="Judgment backend. Defaults to intraday_agents.advice_backend.",
    )
    intraday_parser.add_argument(
        "--debate-rounds",
        type=int,
        choices=range(1, 4),
        help="Bull/bear discussion rounds. Defaults to intraday_agents.debate_rounds.",
    )
    intraday_parser.add_argument(
        "--emit-low-signal",
        action="store_true",
        help="Also emit routine low-confidence observations with no evidence or material price move.",
    )
    intraday_parser.set_defaults(func=command_intraday)

    operation_parser = subparsers.add_parser("operation", help="Record a user-confirmed operation.")
    operation_parser.add_argument("--market", choices=["a_share", "us_equities"], required=True)
    operation_parser.add_argument("--symbol", required=True)
    operation_parser.add_argument("--action", choices=["buy", "sell", "hold", "skip"], required=True)
    operation_parser.add_argument("--quantity", type=float, required=True)
    operation_parser.add_argument("--price", type=float, required=True)
    operation_parser.add_argument("--at", help="Operation timestamp in ISO 8601.")
    operation_parser.add_argument("--note")
    operation_parser.set_defaults(func=command_operation)

    review_parser = subparsers.add_parser("review", help="Build a post-market review.")
    review_parser.add_argument("--market", choices=["a_share", "us_equities"], required=True)
    review_parser.add_argument("--date", help="Review date in YYYY-MM-DD.")
    review_parser.add_argument("--output")
    review_parser.set_defaults(func=command_review)

    web_parser = subparsers.add_parser("web", help="Run the local portfolio web dashboard.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    web_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    web_parser.set_defaults(func=command_web)

    schedule_parser = subparsers.add_parser("schedule", help="Run briefs on the configured schedule.")
    add_common_run_args(schedule_parser)
    schedule_parser.add_argument(
        "--run-on-start",
        action="store_true",
        help="Run once immediately before waiting for the next configured schedule.",
    )
    schedule_parser.add_argument("--once", action="store_true", help="Run once through the scheduler entrypoint.")
    schedule_parser.set_defaults(func=command_schedule)

    service_parser = subparsers.add_parser(
        "service",
        help="Run briefs and intraday market polling in one long-lived process.",
    )
    add_common_run_args(service_parser)
    service_parser.add_argument(
        "--markets",
        nargs="+",
        choices=["a_share", "us_equities"],
        help="Intraday markets to poll. Defaults to markets with configured holdings.",
    )
    service_parser.add_argument(
        "--no-briefs",
        action="store_true",
        help="Disable scheduled pre-market briefs inside the service.",
    )
    service_parser.add_argument(
        "--with-news",
        action="store_true",
        help="Also collect verified news immediately before each intraday advice cycle.",
    )
    service_parser.add_argument(
        "--no-news-watch",
        action="store_true",
        help="Disable the independent 24-hour evidence collection loop.",
    )
    service_parser.add_argument(
        "--news-interval",
        type=int,
        help="Override the independent evidence collection interval in seconds.",
    )
    service_parser.add_argument(
        "--advice-backend",
        choices=["conservative", "zhipu", "openai"],
        help="Intraday judgment backend. Defaults to intraday_agents.advice_backend.",
    )
    service_parser.add_argument("--interval", type=int, help="Override all market polling intervals in seconds.")
    service_parser.add_argument(
        "--tick-seconds",
        type=int,
        default=15,
        help="How often the service checks schedules and market state.",
    )
    service_parser.add_argument(
        "--debate-rounds",
        type=int,
        choices=range(1, 4),
        help="Bull/bear discussion rounds for model-backed intraday advice.",
    )
    service_parser.add_argument(
        "--emit-low-signal",
        action="store_true",
        help="Also emit routine low-confidence intraday observations.",
    )
    service_parser.set_defaults(func=command_service)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
