from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .config import ensure_dirs, resolve_path


def output_path_for(
    root: Path,
    settings: Mapping[str, Any],
    run_date: date,
    override: str | None = None,
    output_format: str | None = None,
) -> Path:
    if override:
        return resolve_path(root, override)

    obsidian = settings.get("obsidian", {})
    vault_path = str(obsidian.get("vault_path", "")).strip()
    if vault_path:
        note_dir = str(obsidian.get("note_dir", "Daily Research")).strip()
        base_dir = Path(vault_path).expanduser() / note_dir
    else:
        base_dir = resolve_path(root, settings.get("output_dir", "briefs"))

    selected_format = (output_format or str(settings.get("output_format", "markdown"))).strip().lower()
    extension = "html" if selected_format == "html" else "md"
    return base_dir / f"{run_date.isoformat()}-brief.{extension}"


def runs_dir_for(root: Path, settings: Mapping[str, Any]) -> Path:
    return resolve_path(root, settings.get("runs_dir", "runs"))


def source_log_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-source-log.md")


def write_text(path: Path, content: str) -> Path:
    ensure_dirs([path.parent])
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    ensure_dirs([path.parent])
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")
