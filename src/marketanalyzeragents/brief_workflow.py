from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore

from .collectors import collect_evidence, resolve_research_window, window_duration_hours
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
from .intraday import build_outbox
from .openai_runner import OpenAIError, run_openai
from .portfolio_store import PortfolioStore
from .prompting import build_openai_messages
from .scheduler import local_now
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


def market_filtered_inputs(inputs: dict[str, Any], market: str | None) -> dict[str, Any]:
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


def settings_for_market(root: Path, settings: dict[str, Any], market: str | None) -> dict[str, Any]:
    if market is None:
        return settings
    return load_market_settings(root, settings, market)


def store_and_outbox(root: Path, settings: dict[str, Any]) -> tuple[PortfolioStore, Any]:
    state = settings.get("state", {})
    store = PortfolioStore(resolve_path(root, state.get("database_path", "state/portfolio.db")))
    outbox = build_outbox(root, settings)
    return store, outbox


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
        market_summaries=model_brief.market_summaries,
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

    delivery_path = write_brief_outputs(output_path, brief_text)
    print(f"Brief written: {delivery_path}")
    print(f"Source log written: {source_log_path}")
    return 0


def write_brief_outputs(output_path: Path, brief_text: str) -> Path:
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


def brief_delivery_path(output_path: Path) -> Path:
    if output_path.suffix.lower() == ".html":
        return output_path
    html_path = output_path.with_suffix(".html")
    return html_path if html_path.exists() else output_path


def notify_pre_market_brief(
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


def run_brief_command(args: Any) -> int:
    root = find_project_root()
    load_dotenv(root / ".env")
    settings = settings_for_market(root, load_settings(root), getattr(args, "market", None))
    backend = args.backend or settings.get("backend", "zhipu")
    run_date = parse_date(args.date, str(settings.get("timezone", "Asia/Shanghai")))
    inputs = market_filtered_inputs(read_inputs(root, settings), getattr(args, "market", None))
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
        store, _ = store_and_outbox(root, settings)
        try:
            store.save_evidence_pack(pack.to_dict())
        finally:
            store.close()
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
        delivery_path = write_brief_outputs(output_path, brief_text)
        print(f"Brief written without model inference: {delivery_path}")
        print(f"Source log written: {source_log_path}")
        notify_pre_market_brief(root, settings, run_date, delivery_path)
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
            notify_pre_market_brief(root, settings, run_date, brief_delivery_path(output_path))
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
            notify_pre_market_brief(root, settings, run_date, brief_delivery_path(output_path))
        return exit_code

    print(f"Unknown backend: {backend}", file=sys.stderr)
    return 2
