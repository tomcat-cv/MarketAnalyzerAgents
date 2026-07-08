from __future__ import annotations

import argparse
import json
from typing import Sequence

from .analysis_system import generate_intraday_suggestion, generate_market_report, service_loop
from .config import find_project_root
from .env import load_dotenv
from .web import run_web_server


def command_report(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    result = generate_market_report(root, slot=args.slot, backend=args.backend)
    print(json.dumps({key: result[key] for key in ("id", "title", "generated_at", "html_path")}, ensure_ascii=False, indent=2))
    return 0


def command_suggest(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    result = generate_intraday_suggestion(root, backend=args.backend)
    print(json.dumps({key: result[key] for key in ("id", "title", "generated_at", "quote_count")}, ensure_ascii=False, indent=2))
    return 0


def command_web(args: argparse.Namespace) -> int:
    run_web_server(host=args.host, port=args.port, root=find_project_root())
    return 0


def command_service(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    service_loop(root, tick_seconds=args.tick_seconds, run_on_start=args.run_on_start)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketanalyzeragents",
        description="Stock trading assistant: web configuration, scheduled market reports, and intraday suggestions.",
    )
    subparsers = parser.add_subparsers(dest="command")

    web_parser = subparsers.add_parser("web", help="Run the local HTML dashboard.")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.set_defaults(func=command_web)

    report_parser = subparsers.add_parser("report", help="Collect configured sources and generate a market analysis report.")
    report_parser.add_argument("--slot", help="Report slot label, for example 08:00.")
    report_parser.add_argument("--backend", choices=["zhipu", "openai", "dry-run"], help="Override analysis backend.")
    report_parser.set_defaults(func=command_report)

    suggest_parser = subparsers.add_parser("suggest", help="Generate an intraday operation suggestion.")
    suggest_parser.add_argument("--backend", choices=["zhipu", "openai", "dry-run"], help="Override suggestion backend.")
    suggest_parser.set_defaults(func=command_suggest)

    service_parser = subparsers.add_parser("service", help="Run scheduled reports and intraday suggestion polling.")
    service_parser.add_argument("--tick-seconds", type=int, default=30)
    service_parser.add_argument("--run-on-start", action="store_true")
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
