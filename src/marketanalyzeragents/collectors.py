from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Iterable, Mapping

from .collectors_core import (
    CollectionError,
    HttpClient,
    hostname_allowed,
    parse_datetime,
    resolve_research_window,
    strip_html,
    window_duration_hours,
)
from .evidence import EvidenceItem


def _first_text(element: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
        for child in element:
            if child.tag.split("}")[-1] == name.split(":")[-1] and child.text:
                return child.text.strip()
    return ""


def _entry_link(element: ET.Element) -> str:
    direct = _first_text(element, ["link"])
    if direct:
        return direct
    for child in element:
        if child.tag.split("}")[-1] == "link" and child.attrib.get("href"):
            return child.attrib["href"].strip()
    return ""


def collect_rss_items(
    *,
    client: HttpClient,
    feed: Mapping[str, Any],
    cutoff,
    window_end=None,
) -> list[EvidenceItem]:
    name = str(feed["name"])
    url = str(feed["url"])
    allowed_domains = list(feed.get("allowed_domains", []))
    source_type = str(feed.get("source_type", "primary"))
    limit = int(feed.get("limit", 10))
    root = ET.fromstring(client.request_bytes(url))
    elements = [
        element
        for element in root.iter()
        if element.tag.split("}")[-1] in {"item", "entry"}
    ]
    items: list[EvidenceItem] = []
    for element in elements:
        title = strip_html(_first_text(element, ["title"]))
        link = _entry_link(element)
        published = parse_datetime(_first_text(element, ["pubDate", "published", "updated", "dc:date"]))
        summary = strip_html(_first_text(element, ["description", "summary", "content"]))
        if (
            not title
            or not link
            or not published
            or published < cutoff
            or (window_end is not None and published > window_end)
        ):
            continue
        if allowed_domains and not hostname_allowed(link, allowed_domains):
            continue
        evidence_level = "summary" if summary and summary.casefold() != title.casefold() else "title_only"
        items.append(
            EvidenceItem(
                id="",
                title=title,
                published_at=published.isoformat(timespec="seconds"),
                source_name=name,
                source_type=source_type,
                url=link,
                content=summary or f"Official feed item title: {title}. Open the original source to verify details.",
                matched_topics=[str(topic) for topic in feed.get("topics", [])],
                matched_tickers=[str(ticker).upper() for ticker in feed.get("tickers", [])],
                evidence_level=evidence_level,
            )
        )
        if len(items) >= limit:
            break
    return items
