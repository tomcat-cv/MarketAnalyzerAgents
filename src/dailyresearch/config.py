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
    "output_format": "html",
    "max_items": 12,
    "context_path": "config/research-context.md",
    "sources_path": "config/sources.json",
    "feedback_path": "memory/feedback.md",
    "output_dir": "briefs",
    "runs_dir": "runs",
    "obsidian": {"vault_path": "", "note_dir": "Daily Research"},
    "collectors": {
        "user_agent": "dailyresearch/0.1 research@example.com",
        "timeout_seconds": 30,
        "max_evidence_items": 200,
    },
    "openai": {
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-5.4",
        "reasoning_effort": "medium",
    },
    "zhipu": {
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.1",
        "temperature": 0.2,
        "max_tokens": 32768,
        "thinking": "enabled",
    },
    "codex": {"model": "", "sandbox": "workspace-write", "extra_writable_dirs": []},
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

    backend = os.environ.get("DAILYRESEARCH_BACKEND", settings.get("backend", "zhipu"))
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
    if os.environ.get("OBSIDIAN_VAULT_PATH"):
        settings["obsidian"]["vault_path"] = os.environ["OBSIDIAN_VAULT_PATH"]

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
    feedback_path = resolve_path(root, settings["feedback_path"])

    return {
        "context": read_optional_text(context_path),
        "sources": load_json(sources_path, {}),
        "feedback": read_optional_text(feedback_path),
        "paths": {
            "context": str(context_path),
            "sources": str(sources_path),
            "feedback": str(feedback_path),
        },
    }


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
