from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


DEFAULT_SETTINGS: Dict[str, Any] = {
    "backend": "zhipu",
    "model": "glm-5.1",
    "timezone": "Asia/Shanghai",
    "freshness_window": "previous_day_to_run",
    "lookback_hours": 24,
    "language": "zh-CN",
    "max_items": 12,
    "context_path": "config/research-context.md",
    "sources_path": "config/sources.json",
    "output_dir": "briefs",
    "runs_dir": "runs",
    "state": {
        "database_path": "state/portfolio.db",
        "conversation_outbox": "state/conversation-outbox.jsonl",
    },
    "markets": {
        "a_share": {"holidays": [], "poll_interval_seconds": 60},
        "us_equities": {"holidays": [], "poll_interval_seconds": 60},
    },
    "market_data": {
        "provider": "yahoo",
        "history_range": "6mo",
        "history_interval": "1d",
    },
    "intraday_agents": {
        "debate_rounds": 1,
        "max_evidence_items_per_symbol": 8,
        "price_history_points": 20,
    },
    "schedule": {
        "mode": "daily",
        "time": "06:00",
        "interval_minutes": 1440,
        "run_on_start": False,
    },
    "collectors": {
        "user_agent": "market-analyzer-agents/0.1 research@example.com",
        "timeout_seconds": 30,
        "max_retries": 2,
        "retry_backoff_seconds": 1.0,
        "max_evidence_items": 200,
    },
    "service": {
        "health_path": "state/service-health.json",
        "max_backoff_seconds": 300,
        "retention_days": 120,
    },
    "openai": {
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-5.4",
        "reasoning_effort": "medium",
        "max_retries": 2,
        "retry_backoff_seconds": 1.0,
    },
    "zhipu": {
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.1",
        "temperature": 0.2,
        "max_tokens": 32768,
        "thinking": "enabled",
        "max_retries": 2,
        "retry_backoff_seconds": 1.0,
    },
}


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "settings.json").exists():
            return candidate
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return current


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(root: Path) -> Dict[str, Any]:
    settings_path = root / "config" / "settings.json"
    settings = deep_merge(DEFAULT_SETTINGS, load_json(settings_path, {}))

    backend = os.environ.get("MARKET_ANALYZER_AGENTS_BACKEND", settings.get("backend", "zhipu"))
    settings["backend"] = backend

    if os.environ.get("ZHIPU_MODEL"):
        settings["model"] = os.environ["ZHIPU_MODEL"]
        settings["zhipu"]["model"] = os.environ["ZHIPU_MODEL"]
    if os.environ.get("OPENAI_MODEL"):
        settings["openai"]["model"] = os.environ["OPENAI_MODEL"]
        if backend == "openai":
            settings["model"] = os.environ["OPENAI_MODEL"]
    if os.environ.get("ZHIPU_API_BASE"):
        settings["zhipu"]["api_base"] = os.environ["ZHIPU_API_BASE"]
    if os.environ.get("MARKET_ANALYZER_AGENTS_SCHEDULE_MODE"):
        settings["schedule"]["mode"] = os.environ["MARKET_ANALYZER_AGENTS_SCHEDULE_MODE"]
    if os.environ.get("MARKET_ANALYZER_AGENTS_SCHEDULE_TIME"):
        settings["schedule"]["time"] = os.environ["MARKET_ANALYZER_AGENTS_SCHEDULE_TIME"]
    if os.environ.get("MARKET_ANALYZER_AGENTS_INTERVAL_MINUTES"):
        settings["schedule"]["interval_minutes"] = int(os.environ["MARKET_ANALYZER_AGENTS_INTERVAL_MINUTES"])
    if os.environ.get("MARKET_ANALYZER_AGENTS_RUN_ON_START"):
        settings["schedule"]["run_on_start"] = os.environ["MARKET_ANALYZER_AGENTS_RUN_ON_START"].strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if os.environ.get("MARKET_ANALYZER_AGENTS_HEALTH_PATH"):
        settings["service"]["health_path"] = os.environ["MARKET_ANALYZER_AGENTS_HEALTH_PATH"].strip()
    if os.environ.get("MARKET_ANALYZER_AGENTS_RETENTION_DAYS"):
        settings["service"]["retention_days"] = int(os.environ["MARKET_ANALYZER_AGENTS_RETENTION_DAYS"])

    return settings


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_inputs(root: Path, settings: Mapping[str, Any]) -> Dict[str, Any]:
    context_path = resolve_path(root, settings["context_path"])
    sources_path = resolve_path(root, settings["sources_path"])

    return {
        "context": read_optional_text(context_path),
        "sources": load_json(sources_path, {}),
        "paths": {
            "context": str(context_path),
            "sources": str(sources_path),
        },
    }


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
