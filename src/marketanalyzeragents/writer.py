from __future__ import annotations

import json
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ensure_dirs


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
    in_section = False
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
            if level == 1:
                if in_section:
                    body.append("</section>")
                    in_section = False
                body.append(f"<h1>{text}</h1>")
            elif level == 2:
                if in_section:
                    body.append("</section>")
                body.append('<section class="report-section">')
                body.append(f"<h2>{text}</h2>")
                in_section = True
            else:
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
    if in_section:
        body.append("</section>")

    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; max-width: 1080px; margin: 28px auto; padding: 0 20px 48px; color: #17202a; background: #f6f8fa; }}
    h1, h2, h3, h4 {{ line-height: 1.3; letter-spacing: 0; }}
    h1 {{ margin: 0 0 18px; padding-bottom: 14px; border-bottom: 1px solid #d8dee4; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 21px; }}
    h3 {{ margin: 20px 0 8px; font-size: 17px; color: #344054; }}
    h4 {{ margin: 16px 0 6px; font-size: 15px; color: #344054; }}
    p {{ margin: 8px 0; }}
    ul {{ margin: 8px 0 0; padding-left: 22px; }}
    li {{ margin: 6px 0; }}
    a {{ color: #0969da; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .report-section {{ background: #fff; border: 1px solid #d8dee4; border-radius: 8px; padding: 18px 20px; margin: 14px 0; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }}
    .report-section > h2 {{ border-bottom: 1px solid #eef2f6; padding-bottom: 10px; }}
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
