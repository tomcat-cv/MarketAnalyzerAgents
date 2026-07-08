from __future__ import annotations

import os
import json
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
    "sources_path": "config/sources.json",
    "market_config_paths": {
        "a_share": "config/markets/a_share.json",
        "us_equities": "config/markets/us_equities.json",
    },
    "state": {
        "analysis_dir": "state/analysis",
    },
    "report_schedule": ["08:00", "14:00", "20:00"],
    "intraday_suggestion_interval_seconds": 1800,
    "markets": {
        "a_share": {
            "calendar": {"provider": "config", "path": "", "strict": False},
            "holidays": [],
            "poll_interval_seconds": 60,
        },
        "us_equities": {
            "calendar": {"provider": "config", "path": "", "strict": False},
            "holidays": [],
            "poll_interval_seconds": 60,
        },
    },
    "market_data": {
        "provider": "yahoo",
        "history_range": "6mo",
        "history_interval": "1d",
    },
    "intraday_agents": {
        "advice_backend": "zhipu",
        "debate_rounds": 1,
    },
    "collectors": {
        "user_agent": "market-analyzer-agents/0.1 research@example.com",
        "timeout_seconds": 30,
        "max_retries": 2,
        "retry_backoff_seconds": 1.0,
        "max_evidence_items": 200,
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
    if os.environ.get("MARKET_ANALYZER_AGENTS_REPORT_SCHEDULE"):
        settings["report_schedule"] = [
            item.strip()
            for item in os.environ["MARKET_ANALYZER_AGENTS_REPORT_SCHEDULE"].split(",")
            if item.strip()
        ]
    if os.environ.get("MARKET_ANALYZER_AGENTS_INTRADAY_INTERVAL_SECONDS"):
        settings["intraday_suggestion_interval_seconds"] = int(
            os.environ["MARKET_ANALYZER_AGENTS_INTRADAY_INTERVAL_SECONDS"]
        )

    return settings


def load_market_settings(root: Path, base_settings: Mapping[str, Any], market: str) -> Dict[str, Any]:
    if market not in {"a_share", "us_equities"}:
        raise ValueError("market must be a_share or us_equities")

    settings = deepcopy(dict(base_settings))
    paths = settings.get("market_config_paths", {})
    market_path = paths.get(market) if isinstance(paths, Mapping) else None
    if not market_path:
        return settings

    market_config = load_json(resolve_path(root, str(market_path)), {})
    if not isinstance(market_config, Mapping):
        return settings

    market_values = market_config.get("market", {})
    overrides = {key: value for key, value in market_config.items() if key != "market"}
    settings = deep_merge(settings, overrides)
    settings["active_market"] = market
    settings.setdefault("markets", {})
    if isinstance(market_values, Mapping):
        current = settings.get("markets", {}).get(market, {})
        settings["markets"][market] = deep_merge(current, market_values)
    return settings


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
