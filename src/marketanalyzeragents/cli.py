from __future__ import annotations

import argparse
import json
import os
import sys
import time as time_module
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore

from .collectors import HttpClient, collect_evidence, resolve_research_window, window_duration_hours
from .agent_debate import backend_invoker, run_agent_debate
from .config import (
    ensure_dirs,
    find_project_root,
    load_market_settings,
    load_settings,
    read_inputs,
    resolve_path,
)
from .env import load_dotenv
from .evidence import (
    EvidencePack,
    configured_focus_topics,
    configured_portfolio_holdings,
    evidence_only_brief_markdown,
    evidence_pack_markdown,
    filter_evidence_pack,
    model_summary_brief_markdown,
    new_evidence_pack,
    parse_model_brief,
    source_log_markdown,
    validate_summary_citations,
)
from .intraday import (
    build_outbox,
    build_suggestion,
    fetch_yahoo_market_data,
    market_history_payload,
)
from .market_calendar import market_status
from .openai_runner import OpenAIError, run_openai
from .portfolio_store import PortfolioStore
from .prompting import build_openai_messages
from .scheduler import ScheduleError, local_now, next_run_at, seconds_until
from .review import build_daily_review
from .writer import (
    markdown_to_html,
    output_path_for,
    run_stamp,
    runs_dir_for,
    source_log_path_for,
    write_json,
    write_text,
)
from .zhipu_runner import ZhipuError, run_zhipu


def parse_date(value: str | None, timezone: str) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    if ZoneInfo is None:
        return date.today()
    return datetime.now(ZoneInfo(timezone)).date()


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


def _market_filtered_inputs(inputs: dict[str, Any], market: str | None) -> dict[str, Any]:
    if market is None:
        return inputs

    def matches_market(value: Any) -> bool:
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if market == "us_equities":
            return "A股" not in text
        return "美股" not in text and "S&P 500" not in text and "Nasdaq" not in text

    sources = json.loads(json.dumps(inputs.get("sources", {}), ensure_ascii=False))
    portfolios = sources.get("portfolios", {})
    if isinstance(portfolios, dict):
        for name in ("a_share", "us_equities"):
            if name != market and isinstance(portfolios.get(name), dict):
                portfolios[name]["holdings"] = []
    market_scope = sources.get("market_scope", {})
    if isinstance(market_scope, dict):
        sources["market_scope"] = {market: market_scope.get(market, {})}
    focus_topics = sources.get("focus_topics", [])
    if isinstance(focus_topics, list):
        for topic in focus_topics:
            if not isinstance(topic, dict):
                continue
            if isinstance(topic.get("segments"), list):
                topic["segments"] = [segment for segment in topic["segments"] if matches_market(segment)]
            if isinstance(topic.get("instruments"), list):
                topic["instruments"] = [
                    instrument for instrument in topic["instruments"] if matches_market(instrument)
                ]
    collectors = sources.get("collectors", {})
    if isinstance(collectors, dict):
        yahoo = collectors.get("yahoo_market_snapshots", {})
        if isinstance(yahoo, dict) and isinstance(yahoo.get("instruments"), list):
            yahoo["instruments"] = [
                instrument for instrument in yahoo["instruments"] if matches_market(instrument)
            ]
    filtered = dict(inputs)
    filtered["sources"] = sources
    return filtered


def _settings_for_market(root: Path, settings: dict[str, Any], market: str | None) -> dict[str, Any]:
    if market is None:
        return settings
    return load_market_settings(root, settings, market)


def write_model_brief(
    *,
    result_text: str,
    raw: dict[str, Any],
    payload: dict[str, Any],
    pack: EvidencePack,
    prompt_path: Path,
    system: str,
    user: str,
    runs_dir: Path,
    stamp: str,
    output_path: Path,
    run_date: date,
    holdings: Sequence[dict[str, Any]],
    focus_topics: Sequence[dict[str, Any]],
    market: str | None = None,
) -> int:
    write_text(prompt_path, f"# System\n\n{system}\n\n# User\n\n{user}\n")
    write_json(runs_dir / f"{stamp}-request.json", payload)
    write_json(runs_dir / f"{stamp}-response.json", raw)
    write_text(runs_dir / f"{stamp}-model-output.txt", result_text)

    model_pack = filter_evidence_pack(pack, {"summary"})
    number_warnings: list[str] = []
    try:
        model_brief = parse_model_brief(
            result_text, model_pack, holdings, warnings=number_warnings
        )
    except ValueError as exc:
        write_json(runs_dir / f"{stamp}-validation-errors.json", [str(exc)])
        print(f"Model summary failed structured-output validation: {exc}", file=sys.stderr)
        return 2

    if number_warnings:
        write_json(runs_dir / f"{stamp}-validation-warnings.json", number_warnings)
        for warning in number_warnings:
            print(f"Warning: {warning}", file=sys.stderr)

    brief_text = model_summary_brief_markdown(
        pack,
        model_brief.summaries,
        model_brief.analyses,
        run_date,
        holdings=holdings,
        focus_topics=focus_topics,
        portfolio_actions=model_brief.portfolio_actions,
        market=market,
    )
    write_text(runs_dir / f"{stamp}-brief-source.md", brief_text)
    source_log_path = source_log_path_for(output_path)
    write_text(runs_dir / f"{stamp}-source-log.md", source_log_markdown(pack))
    write_text(source_log_path, source_log_markdown(pack))
    validation_errors = validate_summary_citations(brief_text, pack)
    if validation_errors:
        write_json(runs_dir / f"{stamp}-validation-warnings.json", validation_errors)
        print("Note: brief has citation validation notes (non-blocking):", file=sys.stderr)
        for error in validation_errors:
            print(f"- {error}", file=sys.stderr)

    delivery_path = _write_brief_outputs(output_path, brief_text)
    print(f"Brief written: {delivery_path}")
    print(f"Source log written: {source_log_path}")
    return 0


def _write_brief_outputs(output_path: Path, brief_text: str) -> Path:
    title = brief_text.splitlines()[0].lstrip("# ").strip() if brief_text.splitlines() else "Market Analyzer Brief"
    html_text = markdown_to_html(brief_text, title=title)
    if output_path.suffix.lower() == ".html":
        write_text(output_path.with_suffix(".md"), brief_text)
        write_text(output_path, html_text)
        return output_path
    write_text(output_path, brief_text)
    html_path = output_path.with_suffix(".html")
    write_text(html_path, html_text)
    return html_path


def _brief_delivery_path(output_path: Path) -> Path:
    if output_path.suffix.lower() == ".html":
        return output_path
    html_path = output_path.with_suffix(".html")
    return html_path if html_path.exists() else output_path


def command_run(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = _settings_for_market(root, load_settings(root), getattr(args, "market", None))
    backend = args.backend or settings.get("backend", "zhipu")
    run_date = parse_date(args.date, str(settings.get("timezone", "Asia/Shanghai")))
    inputs = _market_filtered_inputs(read_inputs(root, settings), getattr(args, "market", None))
    holdings = configured_portfolio_holdings(inputs.get("sources", {}))
    focus_topics = configured_focus_topics(inputs.get("sources", {}))
    output_path = output_path_for(root, settings, run_date, args.output)
    runs_dir = runs_dir_for(root, settings)
    ensure_dirs([runs_dir, output_path.parent])

    stamp = run_stamp()
    if backend == "dry-run":
        window_start, window_end, window_mode = resolve_research_window(settings)
        local_tz = ZoneInfo(str(settings.get("timezone", "Asia/Shanghai"))) if ZoneInfo else None
        pack = new_evidence_pack(
            window_duration_hours(window_start, window_end),
            window_start=window_start.astimezone(local_tz) if local_tz else window_start,
            window_end=window_end.astimezone(local_tz) if local_tz else window_end,
            window_mode=window_mode,
            timezone_name=str(settings.get("timezone", "Asia/Shanghai")),
        )
        pack.retrieved_at = "dry-run"
        pack.errors.append("Dry run does not access external evidence sources.")
    else:
        pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
        write_json(runs_dir / f"{stamp}-evidence.json", pack.to_dict())
        write_text(runs_dir / f"{stamp}-evidence.md", evidence_pack_markdown(pack))
        if not pack.items:
            print("No verified evidence was collected; refusing to call the model.", file=sys.stderr)
            for error in pack.errors:
                print(f"- {error}", file=sys.stderr)
            return 2

    model_pack = filter_evidence_pack(pack, {"summary"})
    if backend != "dry-run" and not model_pack.items:
        brief_text = evidence_only_brief_markdown(
            pack,
            run_date,
            holdings=holdings,
            market=getattr(args, "market", None),
        )
        write_text(runs_dir / f"{stamp}-brief-source.md", brief_text)
        source_log_path = source_log_path_for(output_path)
        write_text(runs_dir / f"{stamp}-source-log.md", source_log_markdown(pack))
        write_text(source_log_path, source_log_markdown(pack))
        validation_errors = validate_summary_citations(brief_text, pack)
        if validation_errors:
            write_json(runs_dir / f"{stamp}-validation-errors.json", validation_errors)
            print("Evidence-only brief failed citation validation.", file=sys.stderr)
            return 2
        delivery_path = _write_brief_outputs(output_path, brief_text)
        print(f"Brief written without model inference: {delivery_path}")
        print(f"Source log written: {source_log_path}")
        _notify_pre_market_brief(root, settings, run_date, delivery_path)
        return 0

    if backend != "dry-run":
        write_json(runs_dir / f"{stamp}-model-evidence.json", model_pack.to_dict())
        write_text(runs_dir / f"{stamp}-model-evidence.md", evidence_pack_markdown(model_pack))

    evidence_markdown = evidence_pack_markdown(model_pack)
    system, user = build_openai_messages(
        settings=settings,
        inputs=inputs,
        run_date=run_date,
        evidence_markdown=evidence_markdown,
    )
    prompt_path = runs_dir / f"{stamp}-prompt.md"

    if backend == "dry-run":
        dry_content = f"# System\n\n{system}\n\n# User\n\n{user}\n"
        write_text(prompt_path, dry_content)
        print(f"Dry run prompt written: {prompt_path}")
        print(f"Target brief path: {output_path}")
        return 0

    if backend == "openai":
        try:
            result, payload = run_openai(
                settings=settings,
                system=system,
                user=user,
                model_override=args.model,
            )
        except OpenAIError as exc:
            print(f"OpenAI backend failed: {exc}", file=sys.stderr)
            return 2

        exit_code = write_model_brief(
            result_text=result.text,
            raw=result.raw,
            payload=payload,
            pack=pack,
            prompt_path=prompt_path,
            system=system,
            user=user,
            runs_dir=runs_dir,
            stamp=stamp,
            output_path=output_path,
            run_date=run_date,
            holdings=holdings,
            focus_topics=focus_topics,
            market=getattr(args, "market", None),
        )
        if exit_code == 0:
            _notify_pre_market_brief(root, settings, run_date, _brief_delivery_path(output_path))
        return exit_code

    if backend == "zhipu":
        try:
            result, payload = run_zhipu(
                settings=settings,
                system=system,
                user=user,
                model_override=args.model,
            )
        except ZhipuError as exc:
            print(f"Zhipu backend failed: {exc}", file=sys.stderr)
            return 2

        exit_code = write_model_brief(
            result_text=result.text,
            raw=result.raw,
            payload=payload,
            pack=pack,
            prompt_path=prompt_path,
            system=system,
            user=user,
            runs_dir=runs_dir,
            stamp=stamp,
            output_path=output_path,
            run_date=run_date,
            holdings=holdings,
            focus_topics=focus_topics,
            market=getattr(args, "market", None),
        )
        if exit_code == 0:
            _notify_pre_market_brief(root, settings, run_date, _brief_delivery_path(output_path))
        return exit_code

    print(f"Unknown backend: {backend}", file=sys.stderr)
    return 2


def command_collect(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    inputs = read_inputs(root, settings)
    pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
    output_path = Path(args.output).expanduser() if args.output else root / "runs" / f"{run_stamp()}-evidence.json"
    if not output_path.is_absolute():
        output_path = root / output_path
    write_json(output_path, pack.to_dict())
    print(f"Evidence written: {output_path}")
    print(f"Verified items: {len(pack.items)}")
    print(f"Collection warnings: {len(pack.errors)}")
    print(f"Collector coverage entries: {len(pack.coverage)}")
    return 0 if pack.items else 2


def _notify_pre_market_brief(
    root: Path,
    settings: dict[str, Any],
    run_date: date,
    output_path: Path,
) -> None:
    outbox = build_outbox(root, settings)
    base_url = os.environ.get("MARKET_ANALYZER_AGENTS_BRIEF_BASE_URL", "").strip().rstrip("/")
    url = ""
    output_dir = resolve_path(root, "briefs")
    try:
        relative_output = output_path.relative_to(output_dir)
    except ValueError:
        relative_output = output_path.name
    if base_url:
        url = f"{base_url}/{relative_output.as_posix() if isinstance(relative_output, Path) else relative_output}"
    outbox.deliver(
        {
            "type": "pre_market_brief",
            "market": settings.get("active_market"),
            "date": run_date.isoformat(),
            "output": str(output_path),
            "url": url,
            "generated_at": local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat(timespec="seconds"),
        }
    )


def _store_and_outbox(root: Path, settings: dict[str, Any]) -> tuple[PortfolioStore, Any]:
    state = settings.get("state", {})
    store = PortfolioStore(resolve_path(root, state.get("database_path", "state/portfolio.db")))
    outbox = build_outbox(root, settings)
    return store, outbox


def command_market_status(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_market_settings(root, load_settings(root), args.market)
    market_settings = settings.get("markets", {}).get(args.market, {})
    status = market_status(
        args.market,
        holidays=market_settings.get("holidays", []),
        extra_open_dates=market_settings.get("extra_open_dates", []),
        early_closes=market_settings.get("early_closes", {}),
    )
    print(json.dumps(status.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0


def command_intraday(args: argparse.Namespace) -> int:
    root = find_project_root()
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
    store, outbox = _store_and_outbox(root, settings)
    market_settings = settings.get("markets", {}).get(args.market, {})
    agent_settings = settings.get("intraday_agents", {})
    interval = args.interval or int(market_settings.get("poll_interval_seconds", 60))
    debate_rounds = args.debate_rounds or int(agent_settings.get("debate_rounds", 1))
    max_agent_evidence = int(agent_settings.get("max_evidence_items_per_symbol", 8))
    history_points = int(agent_settings.get("price_history_points", 20))
    market_data_settings = settings.get("market_data", {})
    if market_data_settings.get("provider", "yahoo") != "yahoo":
        print("Only the yahoo market-data provider is currently implemented.", file=sys.stderr)
        return 2
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
        )
        if status.state != "open" and not args.force:
            print(f"{args.market} is {status.state}; no intraday polling performed.")
            return 0
        evidence_by_ticker: dict[str, list[dict[str, Any]]] = {}
        if args.with_news:
            pack = collect_evidence(settings=settings, sources=inputs.get("sources", {}))
            for item in pack.items:
                if item.evidence_level != "summary":
                    continue
                for ticker in item.matched_tickers:
                    evidence_by_ticker.setdefault(ticker.upper(), []).append(item.__dict__)
        failures = []
        for holding in holdings:
            try:
                data = fetch_yahoo_market_data(
                    client,
                    args.market,
                    holding["symbol"],
                    history_range=str(market_data_settings.get("history_range", "6mo")),
                    history_interval=str(market_data_settings.get("history_interval", "1d")),
                )
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
            if args.advice_backend != "conservative":
                debate = run_agent_debate(
                    quote=quote,
                    evidence=evidence,
                    invoke=backend_invoker(settings, args.advice_backend),
                    rounds=debate_rounds,
                    price_history=market_history_payload(data, history_points),
                    portfolio=holding,
                )
                suggestion = debate.suggestion
                turns = debate.turns
            else:
                suggestion = build_suggestion(
                    store, quote, [item["id"] for item in evidence]
                )
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


def _run_once_from_schedule(args: argparse.Namespace) -> int:
    run_args = argparse.Namespace(
        backend=args.backend,
        date=args.date,
        output=args.output,
        model=args.model,
        market=getattr(args, "market", None),
    )
    return command_run(run_args)


def _configured_brief_markets(settings: dict[str, Any], requested_market: str | None = None) -> list[str]:
    if requested_market:
        return [requested_market]
    paths = settings.get("market_config_paths", {})
    if isinstance(paths, dict):
        markets = [market for market in ("a_share", "us_equities") if market in paths]
        if markets:
            return markets
    return []


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


def _configured_intraday_markets(
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


def _write_service_health(
    root: Path,
    settings: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    service_settings = settings.get("service", {})
    path = resolve_path(root, service_settings.get("health_path", "state/service-health.json"))
    write_json(path, payload)


def command_service(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    inputs = read_inputs(root, settings)
    intraday_markets = _configured_intraday_markets(inputs, args.markets)
    brief_markets = [] if args.no_briefs else (args.markets or _configured_brief_markets(settings))
    if not intraday_markets and not brief_markets:
        print("No configured markets. Add holdings, pass --markets, or configure market schedules.", file=sys.stderr)
        return 2

    next_brief_targets: dict[str, datetime] = {}
    last_intraday_poll: dict[str, float] = {}
    next_intraday_allowed: dict[str, float] = {}
    failure_backoff: dict[str, float] = {}
    market_health: dict[str, dict[str, Any]] = {market: {"state": "starting"} for market in intraday_markets}
    last_retention_day = None
    print(
        "Service started. "
        f"Brief schedule enabled={not args.no_briefs}; "
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
                store, _ = _store_and_outbox(root, settings)
                try:
                    pruned = store.prune_market_data(
                        int(service_settings.get("retention_days", 120)),
                    )
                finally:
                    store.close()
                last_retention_day = today
                print(f"Retention cleanup complete: {json.dumps(pruned, ensure_ascii=False)}", flush=True)
            if not args.no_briefs:
                brief_markets = args.markets or _configured_brief_markets(settings)
                for market in brief_markets:
                    market_settings = load_market_settings(root, settings, market)
                    next_target = next_brief_targets.get(market)
                    if next_target is None or next_target <= now:
                        if next_target is not None:
                            exit_code = _run_once_for_market(args, market)
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

            for market in intraday_markets:
                market_settings = load_market_settings(root, settings, market).get("markets", {}).get(market, {})
                status = market_status(
                    market,
                    holidays=market_settings.get("holidays", []),
                    extra_open_dates=market_settings.get("extra_open_dates", []),
                    early_closes=market_settings.get("early_closes", {}),
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
                )
                exit_code = command_intraday(intraday_args)
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
            _write_service_health(
                root,
                settings,
                {
                    "status": "running",
                    "updated_at": local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat(),
                    "brief_schedule_enabled": not args.no_briefs,
                    "next_brief_targets": {
                        market: target.isoformat() for market, target in next_brief_targets.items()
                    },
                    "markets": market_health,
                },
            )
            time_module.sleep(args.tick_seconds)
        except KeyboardInterrupt:
            _write_service_health(
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
        default="conservative",
        help="Judgment backend. Conservative mode never emits directional advice.",
    )
    intraday_parser.add_argument(
        "--debate-rounds",
        type=int,
        choices=range(1, 4),
        help="Bull/bear discussion rounds. Defaults to intraday_agents.debate_rounds.",
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
        help="Collect verified news before each intraday advice cycle.",
    )
    service_parser.add_argument(
        "--advice-backend",
        choices=["conservative", "zhipu", "openai"],
        default="conservative",
        help="Intraday judgment backend. Conservative mode never emits directional advice.",
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
