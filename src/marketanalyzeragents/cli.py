from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import sys
import time as time_module
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, urljoin

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore

from .collectors import HttpClient, collect_evidence, resolve_research_window, window_duration_hours
from .agent_debate import backend_invoker, run_agent_debate
from .config import ensure_dirs, find_project_root, load_settings, read_inputs, resolve_path
from .codex_runner import CodexRunnerError, run_codex_exec
from .env import load_dotenv
from .evidence import (
    EvidencePack,
    configured_a_share_holdings,
    configured_focus_topics,
    configured_holdings,
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
from .html_renderer import render_html_document
from .intraday import (
    build_suggestion,
    fetch_yahoo_market_data,
    market_history_payload,
)
from .market_calendar import market_status
from .openai_runner import OpenAIError, run_openai
from .feishu_port import build_outbox
from .portfolio_store import PortfolioStore
from .prompting import build_codex_task_prompt, build_openai_messages
from .scheduler import ScheduleError, local_now, next_run_at, seconds_until
from .review import build_daily_review
from .writer import output_path_for, run_stamp, runs_dir_for, source_log_path_for, write_json, write_text
from .zhipu_runner import ZhipuError, run_zhipu


class _BriefTextExtractor(HTMLParser):
    _BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "div", "section"}

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._article_depth = 0
        self._link_href = ""
        self.parts: list[str] = []
        self.article_parts: list[str] = []
        self.blocks: list[list[dict[str, str]]] = []
        self.article_blocks: list[list[dict[str, str]]] = []
        self._current_block: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = {
            class_name
            for name, value in attrs
            if name == "class" and value
            for class_name in value.split()
        }
        if tag in {"script", "style", "noscript"} or "footer-note" in classes:
            self._ignored_depth += 1
        if tag == "article":
            self._flush_block()
            self._article_depth = 1
        elif self._article_depth:
            self._article_depth += 1
        if tag in self._BLOCK_TAGS:
            self._flush_block()
        if tag == "a":
            attrs_by_name = {name: value for name, value in attrs}
            self._link_href = str(attrs_by_name.get("href") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._flush_block()
        if tag == "a":
            self._link_href = ""
        if self._ignored_depth:
            self._ignored_depth -= 1
        if self._article_depth:
            self._article_depth -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and not self._ignored_depth:
            self.parts.append(text)
            if self._article_depth:
                self.article_parts.append(text)
            if self._link_href:
                self._current_block.append({"tag": "a", "text": text, "href": self._link_href})
            else:
                self._current_block.append({"tag": "text", "text": text})

    def close(self) -> None:
        super().close()
        self._flush_block()

    def _flush_block(self) -> None:
        if not self._current_block:
            return
        block = self._current_block
        self.blocks.append(block)
        if self._article_depth:
            self.article_blocks.append(block)
        self._current_block = []


def parse_date(value: str | None, timezone: str) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    if ZoneInfo is None:
        return date.today()
    return datetime.now(ZoneInfo(timezone)).date()


def add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=["zhipu", "openai", "codex", "dry-run"],
        help="Execution backend. Defaults to config/settings.json.",
    )
    parser.add_argument("--date", help="Run date in YYYY-MM-DD format.")
    parser.add_argument("--output", help="Override output brief path.")
    parser.add_argument(
        "--format",
        choices=["html", "markdown"],
        help="Output format. Defaults to config/settings.json.",
    )
    parser.add_argument("--model", help="Override backend model for this run.")


def resolve_output_format(args: argparse.Namespace, settings: dict[str, Any]) -> str:
    return (args.format or settings.get("output_format", "markdown")).strip().lower()


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
    output_format: str,
    run_date: date,
    holdings: Sequence[dict[str, Any]],
    focus_topics: Sequence[dict[str, Any]],
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

    if output_format == "html":
        write_text(output_path, render_html_document(brief_text))
    else:
        write_text(output_path, brief_text)
    print(f"Brief written: {output_path}")
    print(f"Source log written: {source_log_path}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    backend = args.backend or settings.get("backend", "zhipu")
    output_format = resolve_output_format(args, settings)
    run_date = parse_date(args.date, str(settings.get("timezone", "Asia/Shanghai")))
    inputs = read_inputs(root, settings)
    holdings = configured_portfolio_holdings(inputs.get("sources", {}))
    focus_topics = configured_focus_topics(inputs.get("sources", {}))
    output_path = output_path_for(root, settings, run_date, args.output, output_format)
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
        brief_text = evidence_only_brief_markdown(pack, run_date, holdings=holdings)
        write_text(runs_dir / f"{stamp}-brief-source.md", brief_text)
        source_log_path = source_log_path_for(output_path)
        write_text(runs_dir / f"{stamp}-source-log.md", source_log_markdown(pack))
        write_text(source_log_path, source_log_markdown(pack))
        validation_errors = validate_summary_citations(brief_text, pack)
        if validation_errors:
            write_json(runs_dir / f"{stamp}-validation-errors.json", validation_errors)
            print("Evidence-only brief failed citation validation.", file=sys.stderr)
            return 2
        if output_format == "html":
            write_text(output_path, render_html_document(brief_text))
        else:
            write_text(output_path, brief_text)
        print(f"Brief written without model inference: {output_path}")
        print(f"Source log written: {source_log_path}")
        _notify_pre_market_brief(root, settings, run_date, output_path)
        return 0

    if backend != "dry-run":
        write_json(runs_dir / f"{stamp}-model-evidence.json", model_pack.to_dict())
        write_text(runs_dir / f"{stamp}-model-evidence.md", evidence_pack_markdown(model_pack))

    evidence_markdown = evidence_pack_markdown(model_pack)
    system, user = build_openai_messages(
        settings=settings,
        inputs=inputs,
        run_date=run_date,
        output_path=output_path,
        output_format=output_format,
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
            output_format=output_format,
            run_date=run_date,
            holdings=holdings,
            focus_topics=focus_topics,
        )
        if exit_code == 0:
            _notify_pre_market_brief(root, settings, run_date, output_path)
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
            output_format=output_format,
            run_date=run_date,
            holdings=holdings,
            focus_topics=focus_topics,
        )
        if exit_code == 0:
            _notify_pre_market_brief(root, settings, run_date, output_path)
        return exit_code

    if backend == "codex":
        codex_prompt = build_codex_task_prompt(
            settings=settings,
            inputs=inputs,
            run_date=run_date,
            output_path=output_path,
            output_format=output_format,
            evidence_markdown=evidence_pack_markdown(pack),
        )
        write_text(prompt_path, codex_prompt)
        last_message_path = runs_dir / f"{stamp}-codex-last-message.md"
        try:
            result = run_codex_exec(
                root=root,
                prompt=codex_prompt,
                settings=settings,
                last_message_path=last_message_path,
            )
        except CodexRunnerError as exc:
            print(f"Codex backend failed: {exc}", file=sys.stderr)
            return 2

        write_text(runs_dir / f"{stamp}-codex-stdout.txt", result.stdout)
        write_text(runs_dir / f"{stamp}-codex-stderr.txt", result.stderr)
        if result.returncode != 0:
            print(f"Codex backend exited with {result.returncode}", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return result.returncode
        if not output_path.exists():
            print(f"Codex did not write expected brief: {output_path}", file=sys.stderr)
            return 2
        validation_errors = validate_summary_citations(output_path.read_text(encoding="utf-8"), pack)
        if validation_errors:
            write_json(runs_dir / f"{stamp}-validation-errors.json", validation_errors)
            print("Codex brief failed evidence citation validation.", file=sys.stderr)
            return 2
        source_log_path = source_log_path_for(output_path)
        write_text(runs_dir / f"{stamp}-source-log.md", source_log_markdown(pack))
        write_text(source_log_path, source_log_markdown(pack))
        print(f"Codex run complete. Expected brief path: {output_path}")
        print(f"Source log written: {source_log_path}")
        print(f"Last message: {last_message_path}")
        _notify_pre_market_brief(root, settings, run_date, output_path)
        return 0

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


def command_doctor(_: argparse.Namespace) -> int:
    root = find_project_root()
    loaded = load_dotenv(root / ".env")
    settings = load_settings(root)
    inputs = read_inputs(root, settings)
    runs_dir = runs_dir_for(root, settings)
    output_path = output_path_for(
        root,
        settings,
        parse_date(None, str(settings.get("timezone", "Asia/Shanghai"))),
        output_format=str(settings.get("output_format", "markdown")),
    )

    print(f"Project root: {root}")
    print(f"Settings backend: {settings.get('backend')}")
    print(f"Model: {settings.get('model')}")
    print(f"Output format: {settings.get('output_format')}")
    print(f"Loaded .env keys: {', '.join(sorted(loaded)) if loaded else '(none)'}")
    print(f"Context file: {inputs['paths']['context']}")
    print(f"Sources file: {inputs['paths']['sources']}")
    print(f"Feedback file: {inputs['paths']['feedback']}")
    print(f"Prompt overrides file: {inputs['paths']['prompt_extra']}")
    window_start, window_end, window_mode = resolve_research_window(settings)
    local_tz = ZoneInfo(str(settings.get("timezone", "Asia/Shanghai"))) if ZoneInfo else None
    if local_tz:
        window_start = window_start.astimezone(local_tz)
        window_end = window_end.astimezone(local_tz)
    holdings = configured_holdings(inputs.get("sources", {}))
    a_share_holdings = configured_a_share_holdings(inputs.get("sources", {}))
    focus_topics = configured_focus_topics(inputs.get("sources", {}))
    print(f"Freshness window: {window_mode} ({window_start.isoformat()} to {window_end.isoformat()})")
    print(f"Configured US holdings: {', '.join(value['ticker'] for value in holdings) or '(none)'}")
    print(
        "Configured A-share holdings: "
        f"{', '.join(value['ticker'] for value in a_share_holdings) or '(none; placeholder ready)'}"
    )
    print(f"Configured focus topics: {', '.join(value['name'] for value in focus_topics) or '(none)'}")
    print(f"Runs dir: {runs_dir}")
    print(f"Next brief path: {output_path}")
    schedule = settings.get("schedule", {})
    print(
        "Schedule: "
        f"mode={schedule.get('mode', 'daily')}, "
        f"time={schedule.get('time', '06:00')}, "
        f"interval_minutes={schedule.get('interval_minutes', 1440)}, "
        f"run_on_start={schedule.get('run_on_start', False)}"
    )
    return 0


def command_render(args: argparse.Namespace) -> int:
    root = find_project_root()
    input_path = Path(args.input).expanduser()
    if not input_path.is_absolute():
        input_path = root / input_path
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2

    output_path = Path(args.output).expanduser() if args.output else input_path.with_suffix(".html")
    if not output_path.is_absolute():
        output_path = root / output_path

    markdown = input_path.read_text(encoding="utf-8")
    write_text(output_path, render_html_document(markdown))
    print(f"HTML brief written: {output_path}")
    return 0


def command_feedback(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_settings(root)
    feedback_path = Path(inputs_path := read_inputs(root, settings)["paths"]["feedback"])
    ensure_dirs([feedback_path.parent])

    lines = []
    if args.like:
        lines.append("## Likes")
        lines.extend(f"- {item}" for item in args.like)
    if args.dislike:
        lines.append("## Dislikes")
        lines.extend(f"- {item}" for item in args.dislike)
    if args.correction:
        lines.append("## Corrections")
        lines.extend(f"- {item}" for item in args.correction)

    if not lines:
        print("Nothing to add. Use --like, --dislike, or --correction.")
        return 0

    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(f"## Feedback {datetime.now().isoformat(timespec='seconds')}\n")
        for line in lines:
            handle.write(line + "\n")
    print(f"Feedback appended: {inputs_path}")
    return 0


def _text_preview(text: str, *, max_chars: int = 1800) -> str:
    lines = [line.strip() for line in text.splitlines()]
    preview_lines = [line for line in lines if line][:24]
    return "\n".join(preview_lines)[:max_chars]


def _html_notification_preview(text: str) -> str:
    parser = _BriefTextExtractor()
    parser.feed(text)
    parser.close()
    parts = parser.article_parts or parser.parts
    return _text_preview("\n".join(parts), max_chars=1800)


def _html_notification_blocks(text: str) -> list[list[dict[str, str]]]:
    parser = _BriefTextExtractor()
    parser.feed(text)
    parser.close()
    blocks = parser.article_blocks or parser.blocks
    return blocks[:24]


def _brief_notification_preview(output_path: Path) -> str:
    if not output_path.exists():
        return ""
    text = output_path.read_text(encoding="utf-8")
    if output_path.suffix.lower() in {".html", ".htm"}:
        return _html_notification_preview(text)
    return _text_preview(text)


def _brief_notification_blocks(output_path: Path) -> list[list[dict[str, str]]]:
    if not output_path.exists() or output_path.suffix.lower() not in {".html", ".htm"}:
        return []
    return _html_notification_blocks(output_path.read_text(encoding="utf-8"))


def _brief_public_url(root: Path, settings: dict[str, Any], output_path: Path) -> str:
    base_url = str(settings.get("web", {}).get("brief_base_url", "")).strip()
    if not base_url:
        return ""
    output_dir = resolve_path(root, settings.get("output_dir", "briefs"))
    try:
        relative = output_path.resolve().relative_to(output_dir.resolve())
    except ValueError:
        relative = Path(output_path.name)
    quoted = "/".join(quote(part) for part in relative.parts)
    return urljoin(base_url.rstrip("/") + "/", quoted)


def _notify_pre_market_brief(
    root: Path,
    settings: dict[str, Any],
    run_date: date,
    output_path: Path,
) -> None:
    outbox = build_outbox(root, settings)
    outbox.deliver(
        {
            "type": "pre_market_brief",
            "date": run_date.isoformat(),
            "output": str(output_path),
            "format": output_path.suffix.lstrip("."),
            "brief_url": _brief_public_url(root, settings, output_path),
            "generated_at": local_now(str(settings.get("timezone", "Asia/Shanghai"))).isoformat(timespec="seconds"),
            "preview": _brief_notification_preview(output_path),
            "preview_blocks": _brief_notification_blocks(output_path),
        }
    )


def _store_and_outbox(root: Path, settings: dict[str, Any]) -> tuple[PortfolioStore, Any]:
    state = settings.get("state", {})
    store = PortfolioStore(resolve_path(root, state.get("database_path", "state/portfolio.db")))
    outbox = build_outbox(root, settings)
    return store, outbox


def command_market_status(args: argparse.Namespace) -> int:
    root = find_project_root()
    settings = load_settings(root)
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
    settings = load_settings(root)
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
        format=args.format,
        model=args.model,
    )
    return command_run(run_args)


def command_schedule(args: argparse.Namespace) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = load_settings(root)
    if args.once:
        return _run_once_from_schedule(args)

    schedule = settings.get("schedule", {})
    if args.run_on_start or bool(schedule.get("run_on_start", False)):
        exit_code = _run_once_from_schedule(args)
        if exit_code != 0:
            print(f"Scheduled startup run failed with exit code {exit_code}.", file=sys.stderr)

    print("Scheduler started. Press Ctrl+C to stop.", flush=True)
    while True:
        try:
            settings = load_settings(root)
            now = local_now(str(settings.get("timezone", "Asia/Shanghai")))
            target = next_run_at(now, settings)
            wait_seconds = seconds_until(target, now)
        except ScheduleError as exc:
            print(f"Invalid schedule configuration: {exc}", file=sys.stderr)
            return 2

        print(f"Next scheduled run: {target.isoformat(timespec='seconds')}", flush=True)
        try:
            time_module.sleep(wait_seconds)
        except KeyboardInterrupt:
            print("Scheduler stopped.")
            return 130

        exit_code = _run_once_from_schedule(args)
        if exit_code != 0:
            print(f"Scheduled run failed with exit code {exit_code}.", file=sys.stderr)


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
    markets = _configured_intraday_markets(inputs, args.markets)
    if not markets:
        print("No configured intraday markets. Add holdings or pass --markets.", file=sys.stderr)
        return 2

    next_brief_target = None
    last_intraday_poll: dict[str, float] = {}
    next_intraday_allowed: dict[str, float] = {}
    failure_backoff: dict[str, float] = {}
    market_health: dict[str, dict[str, Any]] = {market: {"state": "starting"} for market in markets}
    last_retention_day = None
    print(
        "Service started. "
        f"Brief schedule enabled={not args.no_briefs}; "
        f"intraday markets={', '.join(markets)}.",
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
                if next_brief_target is None or next_brief_target <= now:
                    if next_brief_target is not None:
                        exit_code = _run_once_from_schedule(args)
                        if exit_code != 0:
                            print(
                                f"Scheduled brief failed with exit code {exit_code}.",
                                file=sys.stderr,
                            )
                    next_brief_target = next_run_at(now, settings)
                    print(
                        f"Next scheduled brief: {next_brief_target.isoformat(timespec='seconds')}",
                        flush=True,
                    )

            for market in markets:
                market_settings = settings.get("markets", {}).get(market, {})
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
                    "next_brief_target": next_brief_target.isoformat() if next_brief_target else None,
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

    render_parser = subparsers.add_parser("render", help="Render an existing Markdown brief to HTML.")
    render_parser.add_argument("input", help="Input Markdown brief path.")
    render_parser.add_argument("--output", help="Output HTML path. Defaults to input path with .html suffix.")
    render_parser.set_defaults(func=command_render)

    doctor_parser = subparsers.add_parser("doctor", help="Print configuration diagnostics.")
    doctor_parser.set_defaults(func=command_doctor)

    feedback_parser = subparsers.add_parser("feedback", help="Append calibration feedback.")
    feedback_parser.add_argument("--like", action="append", help="Positive preference to remember.")
    feedback_parser.add_argument("--dislike", action="append", help="Negative preference to remember.")
    feedback_parser.add_argument("--correction", action="append", help="Correction to remember.")
    feedback_parser.set_defaults(func=command_feedback)

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
