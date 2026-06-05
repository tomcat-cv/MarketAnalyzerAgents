from __future__ import annotations

import argparse
import sys
import time as time_module
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore

from .collectors import collect_evidence, resolve_research_window, window_duration_hours
from .config import ensure_dirs, find_project_root, load_settings, read_inputs
from .codex_runner import CodexRunnerError, run_codex_exec
from .env import load_dotenv
from .evidence import (
    EvidencePack,
    configured_a_share_holdings,
    configured_holdings,
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
from .openai_runner import OpenAIError, run_openai
from .prompting import build_codex_task_prompt, build_openai_messages
from .scheduler import ScheduleError, local_now, next_run_at, seconds_until
from .writer import output_path_for, run_stamp, runs_dir_for, source_log_path_for, write_json, write_text
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
) -> int:
    write_text(prompt_path, f"# System\n\n{system}\n\n# User\n\n{user}\n")
    write_json(runs_dir / f"{stamp}-request.json", payload)
    write_json(runs_dir / f"{stamp}-response.json", raw)
    write_text(runs_dir / f"{stamp}-model-output.txt", result_text)

    model_pack = filter_evidence_pack(pack, {"summary"})
    try:
        model_brief = parse_model_brief(result_text, model_pack, holdings)
    except ValueError as exc:
        write_json(runs_dir / f"{stamp}-validation-errors.json", [str(exc)])
        print(f"Model summary failed structured-output validation: {exc}", file=sys.stderr)
        return 2

    brief_text = model_summary_brief_markdown(
        pack,
        model_brief.summaries,
        model_brief.analyses,
        run_date,
        holdings=holdings,
        portfolio_actions=model_brief.portfolio_actions,
    )
    write_text(runs_dir / f"{stamp}-brief-source.md", brief_text)
    source_log_path = source_log_path_for(output_path)
    write_text(runs_dir / f"{stamp}-source-log.md", source_log_markdown(pack))
    write_text(source_log_path, source_log_markdown(pack))
    validation_errors = validate_summary_citations(brief_text, pack)
    if validation_errors:
        write_json(runs_dir / f"{stamp}-validation-errors.json", validation_errors)
        print("Generated brief failed evidence citation validation:", file=sys.stderr)
        for error in validation_errors:
            print(f"- {error}", file=sys.stderr)
        return 2

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
    holdings = configured_holdings(inputs.get("sources", {}))
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

        return write_model_brief(
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
        )

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

        return write_model_brief(
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
        )

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
    print(f"Freshness window: {window_mode} ({window_start.isoformat()} to {window_end.isoformat()})")
    print(f"Configured US holdings: {', '.join(value['ticker'] for value in holdings) or '(none)'}")
    print(
        "Configured A-share holdings: "
        f"{', '.join(value['ticker'] for value in a_share_holdings) or '(none; placeholder ready)'}"
    )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dailyresearch",
        description="Create a Codex-friendly daily research brief without Claude or n8n.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Generate a daily research brief.")
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

    schedule_parser = subparsers.add_parser("schedule", help="Run briefs on the configured schedule.")
    add_common_run_args(schedule_parser)
    schedule_parser.add_argument(
        "--run-on-start",
        action="store_true",
        help="Run once immediately before waiting for the next configured schedule.",
    )
    schedule_parser.add_argument("--once", action="store_true", help="Run once through the scheduler entrypoint.")
    schedule_parser.set_defaults(func=command_schedule)

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
