from __future__ import annotations

import json
import html
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .config import ensure_dirs, resolve_path


def output_path_for(
    root: Path,
    settings: Mapping[str, Any],
    run_date: date,
    override: str | None = None,
) -> Path:
    if override:
        return resolve_path(root, override)

    base_dir = resolve_path(root, settings.get("output_dir", "briefs"))
    return base_dir / f"{run_date.isoformat()}-brief.md"


def runs_dir_for(root: Path, settings: Mapping[str, Any]) -> Path:
    return resolve_path(root, settings.get("runs_dir", "runs"))


def source_log_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-source-log.md")


def _inline_markdown_to_html(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_html(markdown: str, title: str = "Market Analyzer Brief") -> str:
    lines = markdown.splitlines()
    body: list[str] = []
    in_list = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("#"):
            if in_list:
                body.append("</ul>")
                in_list = False
            level = min(len(line) - len(line.lstrip("#")), 6)
            text = _inline_markdown_to_html(line[level:].strip())
            body.append(f"<h{level}>{text}</h{level}>")
            continue
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline_markdown_to_html(line[2:].strip())}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        body.append(f"<p>{_inline_markdown_to_html(line)}</p>")
    if in_list:
        body.append("</ul>")

    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; max-width: 980px; margin: 32px auto; padding: 0 20px; color: #17202a; }}
    h1, h2, h3, h4 {{ line-height: 1.3; }}
    h1 {{ border-bottom: 1px solid #d8dee4; padding-bottom: 12px; }}
    li {{ margin: 6px 0; }}
  </style>
</head>
<body>
{body}
</body>
</html>
""".format(title=html.escape(title), body="\n".join(body))


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


def write_json_atomic(path: Path, payload: Any) -> Path:
    ensure_dirs([path.parent])
    tmp_path = path.with_name(f".{path.name}.{run_stamp()}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")
