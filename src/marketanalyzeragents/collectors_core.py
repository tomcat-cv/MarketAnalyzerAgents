from __future__ import annotations

import html
import json
import re
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterable, List, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


class CollectionError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        value = data.strip()
        if value:
            self.parts.append(value)


def strip_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(value or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def parse_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def hostname_allowed(url: str, allowed_domains: Sequence[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return any(hostname == domain.lower() or hostname.endswith(f".{domain.lower()}") for domain in allowed_domains)


def _local_timezone(name: str):
    if ZoneInfo is None:
        return timezone.utc
    return ZoneInfo(name)


def resolve_research_window(
    settings: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)

    mode = str(settings.get("freshness_window", "previous_day_to_run"))
    if mode == "previous_day_to_run":
        local_tz = _local_timezone(str(settings.get("timezone", "Asia/Shanghai")))
        local_end = end.astimezone(local_tz)
        local_start = datetime.combine(
            local_end.date() - timedelta(days=1),
            time.min,
            tzinfo=local_tz,
        )
        start = local_start.astimezone(timezone.utc)
    else:
        start = end - timedelta(hours=int(settings.get("lookback_hours", 24)))
        mode = "rolling_hours"
    return start, end, mode


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        timeout: int = 30,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    def request_bytes(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        last_error: CollectionError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = CollectionError(f"HTTP {exc.code} for {url}: {detail}")
                if exc.code != 429 and exc.code < 500:
                    raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = CollectionError(f"Could not fetch {url}: {exc.reason}")
            if attempt < self.max_retries:
                time_module.sleep(self.retry_backoff_seconds * (2 ** attempt))
        assert last_error is not None
        raise last_error

    def get_json(self, url: str) -> Any:
        return json.loads(self.request_bytes(url).decode("utf-8"))

    def get_text(self, url: str) -> str:
        return self.request_bytes(url).decode("utf-8", errors="replace")

    def post_form_json(self, url: str, values: Mapping[str, str], headers: Mapping[str, str]) -> Any:
        body = urllib.parse.urlencode(values).encode("utf-8")
        merged_headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        merged_headers.update(headers)
        return json.loads(self.request_bytes(url, method="POST", body=body, headers=merged_headers).decode("utf-8"))
