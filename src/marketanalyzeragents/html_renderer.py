from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable, List, Tuple


TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
URL_RE = re.compile(r"https?://[^\s)>\"]+")


def _slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value.lower(), flags=re.UNICODE)
    return value.strip("-") or "section"


def _stash(tokens: List[str], value: str) -> str:
    tokens.append(value)
    return f"\u0000{len(tokens) - 1}\u0000"


def _restore_tokens(value: str, tokens: List[str]) -> str:
    for index, token in enumerate(tokens):
        value = value.replace(f"\u0000{index}\u0000", token)
    return value


def render_inline(value: str) -> str:
    tokens: List[str] = []

    def image_repl(match: re.Match[str]) -> str:
        alt = html.escape(match.group(1).strip(), quote=True)
        src = html.escape(match.group(2).strip(), quote=True)
        markup = (
            '<figure class="brief-image">'
            f'<img src="{src}" alt="{alt}" loading="lazy">'
            f"<figcaption>{alt}</figcaption>"
            "</figure>"
        )
        return _stash(tokens, markup)

    def link_repl(match: re.Match[str]) -> str:
        label = html.escape(match.group(1).strip())
        href = html.escape(match.group(2).strip(), quote=True)
        external = href.startswith(("http://", "https://"))
        attrs = ' target="_blank" rel="noopener noreferrer"' if external else ""
        return _stash(tokens, f'<a href="{href}"{attrs}>{label}</a>')

    def code_repl(match: re.Match[str]) -> str:
        code = html.escape(match.group(1))
        return _stash(tokens, f"<code>{code}</code>")

    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_repl, value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, value)
    value = re.sub(r"`([^`]+)`", code_repl, value)

    escaped = html.escape(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return _restore_tokens(escaped, tokens)


def _split_table_row(row: str) -> List[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _render_table(rows: List[str]) -> str:
    if not rows:
        return ""
    header = _split_table_row(rows[0])
    body_rows = [_split_table_row(row) for row in rows[2:]]

    parts = ['<div class="table-wrap"><table>']
    parts.append("<thead><tr>")
    parts.extend(f"<th>{render_inline(cell)}</th>" for cell in header)
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in body_rows:
        parts.append("<tr>")
        parts.extend(f"<td>{render_inline(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _render_list(items: Iterable[str]) -> str:
    parts = ["<ul>"]
    for item in items:
        parts.append(f"<li>{render_inline(item)}</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_blockquote(lines: Iterable[str]) -> str:
    content = " ".join(line.lstrip("> ").strip() for line in lines)
    return f"<blockquote>{render_inline(content)}</blockquote>"


def _render_code_block(lines: Iterable[str], language: str = "") -> str:
    class_name = f' class="language-{html.escape(language, quote=True)}"' if language else ""
    code = html.escape("\n".join(lines))
    return f"<pre><code{class_name}>{code}</code></pre>"


def _extract_title(markdown: str) -> Tuple[str, str]:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            title = match.group(1).strip()
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", title)
            return title, date_match.group(0) if date_match else ""
    return "Market Analyzer Agents Brief", ""


def _extract_section_nav(markdown: str) -> List[Tuple[str, str]]:
    nav: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if not match:
            continue
        label = match.group(1).strip()
        slug = _slug(label)
        if slug in seen:
            continue
        seen.add(slug)
        nav.append((label, slug))
    return nav


def markdown_to_html_body(markdown: str) -> str:
    lines = markdown.splitlines()
    parts: List[str] = []
    list_items: List[str] = []
    paragraph: List[str] = []
    blockquote: List[str] = []
    in_code = False
    code_language = ""
    code_lines: List[str] = []
    section_open = False
    index = 0

    def close_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            parts.append(f"<p>{render_inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_items
        if list_items:
            parts.append(_render_list(list_items))
            list_items = []

    def close_blockquote() -> None:
        nonlocal blockquote
        if blockquote:
            parts.append(_render_blockquote(blockquote))
            blockquote = []

    def close_blocks() -> None:
        close_paragraph()
        close_list()
        close_blockquote()

    while index < len(lines):
        line = lines[index].rstrip()

        if in_code:
            if line.startswith("```"):
                parts.append(_render_code_block(code_lines, code_language))
                in_code = False
                code_language = ""
                code_lines = []
            else:
                code_lines.append(line)
            index += 1
            continue

        if line.startswith("```"):
            close_blocks()
            in_code = True
            code_language = line.strip("`").strip()
            index += 1
            continue

        if not line.strip():
            close_blocks()
            index += 1
            continue

        if line.startswith("|") and index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1].strip()):
            close_blocks()
            table_rows = [line, lines[index + 1].rstrip()]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_rows.append(lines[index].rstrip())
                index += 1
            parts.append(_render_table(table_rows))
            continue

        if line.startswith(">"):
            close_paragraph()
            close_list()
            blockquote.append(line)
            index += 1
            continue

        list_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if list_match:
            close_paragraph()
            close_blockquote()
            list_items.append(list_match.group(1).strip())
            index += 1
            continue

        heading = HEADING_RE.match(line)
        if heading:
            close_blocks()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            rendered = render_inline(text)
            slug = _slug(text)
            if level == 1:
                parts.append(f'<h1 id="{slug}">{rendered}</h1>')
            elif level == 2:
                if section_open:
                    parts.append("</section>")
                parts.append(f'<section class="report-section" id="{slug}"><h2>{rendered}</h2>')
                section_open = True
            else:
                parts.append(f'<h{level} id="{slug}">{rendered}</h{level}>')
            index += 1
            continue

        close_blockquote()
        close_list()
        paragraph.append(line.strip())
        index += 1

    close_blocks()
    if in_code:
        parts.append(_render_code_block(code_lines, code_language))
    if section_open:
        parts.append("</section>")
    return "\n".join(parts)


def render_html_document(markdown: str, *, generated_at: datetime | None = None) -> str:
    title, report_date = _extract_title(markdown)
    generated_at = generated_at or datetime.now()
    nav = _extract_section_nav(markdown)
    source_count = len(set(URL_RE.findall(markdown)))
    body = markdown_to_html_body(markdown)
    nav_html = "".join(f'<a href="#{slug}">{html.escape(label)}</a>' for label, slug in nav)
    report_date_html = html.escape(report_date or "Latest")
    generated_html = html.escape(generated_at.strftime("%Y-%m-%d %H:%M"))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --paper: #fbfaf6;
      --ink: #1b2320;
      --muted: #62706b;
      --line: #d8d0c2;
      --panel: #ffffff;
      --teal: #2f5e5b;
      --amber: #8a672b;
      --rust: #b44d3a;
      --green-soft: #e6f0ec;
      --amber-soft: #f5ead5;
      --rust-soft: #f7e3dd;
      --shadow: 0 18px 52px rgba(36, 49, 47, 0.10);
    }}

    * {{ box-sizing: border-box; }}

    html {{ scroll-behavior: smooth; }}

    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: Aptos, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      font-size: 16px;
      line-height: 1.72;
    }}

    a {{
      color: var(--teal);
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }}

    .page {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }}

    .brief-header {{
      display: block;
      padding-bottom: 28px;
      border-bottom: 1px solid var(--line);
    }}

    .eyebrow {{
      margin: 0 0 12px;
      color: var(--amber);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }}

    .brief-title {{
      margin: 0;
      max-width: 780px;
      font-family: Georgia, "Times New Roman", "Noto Serif CJK SC", serif;
      font-size: clamp(34px, 5vw, 66px);
      line-height: 1.02;
      font-weight: 700;
      letter-spacing: 0;
    }}

    .brief-subtitle {{
      max-width: 680px;
      margin: 18px 0 0;
      color: var(--muted);
      font-size: 17px;
    }}

    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 24px;
    }}

    .meta-item {{
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.54);
      padding: 12px;
      min-height: 74px;
    }}

    .meta-label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}

    .meta-value {{
      display: block;
      margin-top: 4px;
      font-size: 18px;
      font-weight: 800;
    }}

    .layout {{
      display: grid;
      grid-template-columns: 210px minmax(0, 1fr);
      gap: 28px;
      padding-top: 28px;
    }}

    .brief-nav {{
      position: sticky;
      top: 18px;
      align-self: start;
      border-left: 3px solid var(--teal);
      padding-left: 14px;
    }}

    .brief-nav h2 {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}

    .brief-nav a {{
      display: block;
      padding: 7px 0;
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      text-decoration: none;
    }}

    .brief-nav a:hover {{ color: var(--teal); }}

    article {{
      min-width: 0;
    }}

    article > h1 {{
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }}

    .report-section {{
      padding: 26px 0 34px;
      border-bottom: 1px solid var(--line);
    }}

    .report-section:first-of-type {{ padding-top: 0; }}

    .report-section h2 {{
      margin: 0 0 18px;
      font-family: Georgia, "Times New Roman", "Noto Serif CJK SC", serif;
      font-size: 30px;
      line-height: 1.2;
      letter-spacing: 0;
    }}

    h3 {{
      margin: 30px 0 10px;
      font-size: 21px;
      line-height: 1.35;
    }}

    p {{ margin: 12px 0; }}

    blockquote {{
      margin: 0 0 22px;
      padding: 14px 16px;
      border-left: 4px solid var(--amber);
      background: var(--amber-soft);
      color: #4d3b1e;
    }}

    ul {{
      display: grid;
      gap: 10px;
      margin: 14px 0 0;
      padding: 0;
      list-style: none;
    }}

    li {{
      position: relative;
      padding: 12px 14px 12px 34px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.64);
    }}

    li::before {{
      content: "";
      position: absolute;
      top: 20px;
      left: 15px;
      width: 8px;
      height: 8px;
      background: var(--teal);
    }}

    strong {{ font-weight: 850; }}

    code {{
      padding: 2px 6px;
      border: 1px solid var(--line);
      background: #f3efe6;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 0.9em;
    }}

    pre {{
      overflow-x: auto;
      padding: 16px;
      border: 1px solid var(--line);
      background: #242b28;
      color: #f8f3e8;
    }}

    pre code {{
      padding: 0;
      border: 0;
      background: transparent;
      color: inherit;
    }}

    .table-wrap {{
      overflow-x: auto;
      margin: 18px 0;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 12px 32px rgba(36, 49, 47, 0.06);
    }}

    table {{
      width: 100%;
      min-width: 920px;
      border-collapse: collapse;
      font-size: 14px;
      line-height: 1.55;
    }}

    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}

    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #26332f;
      color: #fffaf0;
      font-size: 12px;
      text-transform: uppercase;
    }}

    tr:nth-child(even) td {{ background: #fbf7ee; }}

    td:first-child {{
      width: 92px;
      font-weight: 900;
    }}

    td:first-child {{
      color: var(--teal);
    }}

    .brief-image {{
      margin: 20px 0;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: var(--shadow);
    }}

    .brief-image img {{
      display: block;
      width: 100%;
      max-height: 520px;
      object-fit: cover;
    }}

    .brief-image figcaption {{
      padding: 10px 12px;
      color: var(--muted);
      font-size: 13px;
    }}

    #executive-summary ul {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    #executive-summary li:nth-child(3n + 1) {{ background: var(--green-soft); }}
    #executive-summary li:nth-child(3n + 2) {{ background: var(--amber-soft); }}
    #executive-summary li:nth-child(3n + 3) {{ background: var(--rust-soft); }}

    #source-log ul {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    #source-log li {{
      overflow-wrap: anywhere;
      font-size: 14px;
    }}

    .footer-note {{
      margin-top: 28px;
      color: var(--muted);
      font-size: 13px;
    }}

    @media (max-width: 900px) {{
      .layout {{
        grid-template-columns: 1fr;
      }}

      .brief-nav {{
        position: static;
        display: flex;
        gap: 10px;
        overflow-x: auto;
        border-left: 0;
        border-bottom: 1px solid var(--line);
        padding: 0 0 12px;
      }}

      .brief-nav h2 {{ display: none; }}
      .brief-nav a {{ white-space: nowrap; }}
      .meta-grid,
      #executive-summary ul,
      #source-log ul {{
        grid-template-columns: 1fr;
      }}
    }}

    @media print {{
      body {{ background: #fff; }}
      .brief-nav {{ display: none; }}
      .layout {{ display: block; }}
      .page {{ width: 100%; padding: 0; }}
      a {{ color: #000; }}
      .report-section {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="brief-header">
      <div>
        <p class="eyebrow">Daily Market Research</p>
        <h1 class="brief-title">{html.escape(title)}</h1>
        <p class="brief-subtitle">A source-backed market brief focused on AI infrastructure, semiconductors, energy, A-shares, and metals.</p>
        <div class="meta-grid" aria-label="Brief metadata">
          <div class="meta-item"><span class="meta-label">Report Date</span><span class="meta-value">{report_date_html}</span></div>
          <div class="meta-item"><span class="meta-label">Sources</span><span class="meta-value">{source_count}</span></div>
          <div class="meta-item"><span class="meta-label">Generated</span><span class="meta-value">{generated_html}</span></div>
        </div>
      </div>
    </header>
    <div class="layout">
      <nav class="brief-nav" aria-label="Brief sections">
        <h2>Sections</h2>
        {nav_html}
      </nav>
      <article>
        {body}
        <p class="footer-note">Generated by Market Analyzer Agents. Verify time-sensitive market data before trading decisions.</p>
      </article>
    </div>
  </main>
</body>
</html>
"""
