from __future__ import annotations

import json
import mimetypes
import sqlite3
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .collectors import HttpClient
from .config import find_project_root, load_json, load_market_settings, load_settings, resolve_path
from .evidence import configured_focus_topics, configured_portfolio_holdings
from .feishu_import import confirm_feishu_portfolio_import, create_feishu_portfolio_import
from .intraday import MarketDataProviderError, fetch_market_data
from .market_calendar import calendar_from_settings, market_status
from .portfolio_snapshots import apply_current_portfolio_snapshots, normalize_holding, save_confirmed_snapshot
from .portfolio_store import PortfolioStore
from .writer import markdown_to_html, write_json_atomic


MARKETS = ("a_share", "us_equities")


def _display_timezone(settings: Mapping[str, Any]) -> str:
    return str(settings.get("timezone", "Asia/Shanghai"))


def _display_zoneinfo(settings: Mapping[str, Any]) -> ZoneInfo:
    return ZoneInfo(_display_timezone(settings))


STATIC_DIR = Path(__file__).with_name("static")


def _load_static_text(name: str, fallback: str = "") -> str:
    path = STATIC_DIR / name
    if not path.exists():
        return fallback
    return path.read_text(encoding="utf-8")


INDEX_HTML = _load_static_text("index.html")


def _read_sources(root: Path, settings: Mapping[str, Any]) -> dict[str, Any]:
    path = resolve_path(root, settings.get("sources_path", "config/sources.json"))
    value = load_json(path, {})
    if not isinstance(value, dict):
        return {}
    return apply_current_portfolio_snapshots(root, settings, value)


def _sources_path(root: Path, settings: Mapping[str, Any]) -> Path:
    return resolve_path(root, settings.get("sources_path", "config/sources.json"))


def _settings_path(root: Path) -> Path:
    return root / "config" / "settings.json"


def _write_config_json(path: Path, payload: Mapping[str, Any]) -> Path:
    if not isinstance(payload, Mapping):
        raise ValueError("configuration payload must be a JSON object")
    return write_json_atomic(path, dict(payload))


def _model_configuration(settings: Mapping[str, Any]) -> dict[str, Any]:
    backend = str(settings.get("backend", "zhipu"))
    openai_settings = settings.get("openai", {})
    zhipu_settings = settings.get("zhipu", {})
    agent_settings = settings.get("intraday_agents", {})
    if not isinstance(openai_settings, Mapping):
        openai_settings = {}
    if not isinstance(zhipu_settings, Mapping):
        zhipu_settings = {}
    if not isinstance(agent_settings, Mapping):
        agent_settings = {}
    active_provider_settings = settings.get(backend, {})
    if not isinstance(active_provider_settings, Mapping):
        active_provider_settings = {}
    return {
        "backend": backend,
        "model": str(active_provider_settings.get("model") or settings.get("model", "")),
        "openai": {
            "api_base": str(openai_settings.get("api_base", "")),
            "model": str(openai_settings.get("model", "")),
            "reasoning_effort": str(openai_settings.get("reasoning_effort", "medium")),
        },
        "zhipu": {
            "api_base": str(zhipu_settings.get("api_base", "")),
            "model": str(zhipu_settings.get("model", "")),
            "temperature": zhipu_settings.get("temperature", 0.2),
            "max_tokens": zhipu_settings.get("max_tokens", 32768),
            "thinking": str(zhipu_settings.get("thinking", "enabled")),
        },
        "intraday_agents": {
            "advice_backend": str(agent_settings.get("advice_backend", "conservative")),
            "debate_rounds": int(agent_settings.get("debate_rounds", 1)),
        },
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace(",", "\n").splitlines()
    elif isinstance(value, list):
        candidates = value
    else:
        return []
    return [str(item).strip() for item in candidates if str(item).strip()]


def _normalized_topic(payload: Mapping[str, Any]) -> dict[str, Any]:
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    name = str(payload.get("name", "")).strip() or topic_id
    segments = []
    raw_segments = payload.get("segments", [])
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, Mapping):
                continue
            segment_name = str(segment.get("name", "")).strip()
            topics = _string_list(segment.get("topics", []))
            if segment_name and topics:
                segments.append({"name": segment_name, "topics": topics})
    instruments = []
    raw_instruments = payload.get("instruments", [])
    if isinstance(raw_instruments, list):
        for instrument in raw_instruments:
            if not isinstance(instrument, Mapping):
                continue
            symbol = str(instrument.get("symbol", "")).strip()
            if not symbol:
                continue
            instruments.append(
                {
                    "symbol": symbol,
                    "name": str(instrument.get("name", "")).strip() or symbol,
                    "topics": _string_list(instrument.get("topics", [])),
                }
            )
    return {"id": topic_id, "name": name, "segments": segments, "instruments": instruments}


def update_model_configuration(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path = _settings_path(root)
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}

    backend = str(payload.get("backend", settings.get("backend", "zhipu"))).strip()
    if backend not in {"zhipu", "openai", "dry-run"}:
        raise ValueError("backend must be zhipu, openai, or dry-run")
    settings["backend"] = backend
    for key in ("openai", "zhipu", "intraday_agents"):
        if not isinstance(settings.get(key, {}), dict):
            settings[key] = {}

    openai_model = str(payload.get("openai_model", "")).strip()
    zhipu_model = str(payload.get("zhipu_model", "")).strip()
    selected_model = str(payload.get("model", "")).strip()
    if openai_model:
        settings.setdefault("openai", {})["model"] = openai_model
    if zhipu_model:
        settings.setdefault("zhipu", {})["model"] = zhipu_model
    if backend == "openai" and (selected_model or openai_model):
        settings.setdefault("openai", {})["model"] = selected_model or openai_model
        settings["model"] = selected_model or openai_model
    if backend == "zhipu" and (selected_model or zhipu_model):
        settings.setdefault("zhipu", {})["model"] = selected_model or zhipu_model
        settings["model"] = selected_model or zhipu_model

    advice_backend = str(payload.get("advice_backend", "")).strip()
    if advice_backend:
        if advice_backend not in {"conservative", "zhipu", "openai"}:
            raise ValueError("advice_backend must be conservative, zhipu, or openai")
        settings.setdefault("intraday_agents", {})["advice_backend"] = advice_backend
    if str(payload.get("debate_rounds", "")).strip():
        debate_rounds = int(payload["debate_rounds"])
        if debate_rounds < 1 or debate_rounds > 3:
            raise ValueError("debate_rounds must be between 1 and 3")
        settings.setdefault("intraday_agents", {})["debate_rounds"] = debate_rounds

    _write_config_json(path, settings)
    return _model_configuration(load_settings(root))


def _latest_quotes(root: Path, settings: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    if not db_path.exists():
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(
            """
            SELECT q.* FROM quotes q
            JOIN (
              SELECT market, symbol, max(observed_at) AS observed_at
              FROM quotes GROUP BY market, symbol
            ) latest
            ON latest.market=q.market AND latest.symbol=q.symbol AND latest.observed_at=q.observed_at
            """
        ).fetchall():
            rows[(row["market"], row["symbol"].upper())] = dict(row)
    return rows


def _refresh_dashboard_quotes(
    root: Path,
    settings: Mapping[str, Any],
    holdings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    market_data_settings = settings.get("market_data", {})
    if not isinstance(market_data_settings, Mapping):
        return []

    state_settings = settings.get("state", {})
    db_path = resolve_path(root, state_settings.get("database_path", "state/portfolio.db"))
    collector_settings = settings.get("collectors", {})
    if not isinstance(collector_settings, Mapping):
        collector_settings = {}
    client = HttpClient(
        str(collector_settings.get("user_agent", "market-analyzer-agents/0.1")),
        int(collector_settings.get("timeout_seconds", 30)),
        int(collector_settings.get("max_retries", 2)),
        float(collector_settings.get("retry_backoff_seconds", 1.0)),
    )
    failures = []
    with PortfolioStore(db_path) as store:
        for holding in holdings:
            market = str(holding.get("market", "")).strip()
            symbol = str(holding.get("symbol") or holding.get("ticker") or "").strip().upper()
            if market not in MARKETS or not symbol:
                continue
            try:
                data = fetch_market_data(
                    client,
                    market,
                    symbol,
                    market_data_settings,
                )
            except MarketDataProviderError as exc:
                failures.append({"market": market, "symbol": symbol, "error": str(exc)})
                break
            except Exception as exc:
                failures.append({"market": market, "symbol": symbol, "error": str(exc)})
                continue
            store.save_quotes([data.quote])
            store.save_price_bars(data.history)
    return failures


def _recent_suggestions(root: Path, settings: Mapping[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    db_path = resolve_path(root, settings.get("state", {}).get("database_path", "state/portfolio.db"))
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT * FROM suggestions
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            return []
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence_ids"] = json.loads(item.pop("evidence_json", "[]"))
        except json.JSONDecodeError:
            item["evidence_ids"] = []
        item["type"] = "intraday_suggestion"
        result.append(item)
    return result


def _outbox_events(root: Path, settings: Mapping[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    path = resolve_path(root, settings.get("state", {}).get("conversation_outbox", "state/conversation-outbox.jsonl"))
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return list(reversed(events))


def _brief_files(root: Path, settings: Mapping[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    brief_root = resolve_path(root, "briefs")
    if not brief_root.exists():
        return []
    display_timezone = _display_timezone(settings)
    display_zone = _display_zoneinfo(settings)
    files = [
        path
        for path in brief_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".html", ".md"}
        and "source-log" not in path.stem
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    result = []
    for path in files[:limit]:
        relative = path.relative_to(brief_root)
        parts = relative.parts
        market = parts[0] if parts and parts[0] in MARKETS else ""
        result.append(
            {
                "name": path.stem,
                "market": market,
                "url": "/briefs/" + urllib.parse.quote(relative.as_posix()),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime, display_zone).isoformat(timespec="seconds"),
                "timezone": display_timezone,
            }
        )
    return result


def load_dashboard_state(root: Path | None = None, *, refresh_quotes: bool = False) -> dict[str, Any]:
    project_root = root or find_project_root()
    settings = load_settings(project_root)
    display_timezone = _display_timezone(settings)
    display_zone = _display_zoneinfo(settings)
    sources = _read_sources(project_root, settings)
    configured_holdings = [dict(holding) for holding in configured_portfolio_holdings(sources)]
    quote_refresh_failures = (
        _refresh_dashboard_quotes(project_root, settings, configured_holdings)
        if refresh_quotes
        else []
    )
    quotes = _latest_quotes(project_root, settings)
    holdings = []
    for holding in configured_holdings:
        copied = dict(holding)
        key = (copied["market"], str(copied.get("symbol", copied["ticker"])).upper())
        copied["quote"] = quotes.get(key)
        holdings.append(copied)

    markets = {}
    for market in MARKETS:
        market_settings = load_market_settings(project_root, settings, market).get("markets", {}).get(market, {})
        status = market_status(
            market,
            holidays=market_settings.get("holidays", []),
            extra_open_dates=market_settings.get("extra_open_dates", []),
            early_closes=market_settings.get("early_closes", {}),
            calendar=calendar_from_settings(market_settings, root=project_root),
        )
        markets[market] = {
            "state": status.state,
            "as_of_beijing": status.as_of_beijing.isoformat(timespec="seconds"),
            "session_open_beijing": status.session_open_beijing.isoformat(timespec="seconds")
            if status.session_open_beijing
            else None,
            "session_close_beijing": status.session_close_beijing.isoformat(timespec="seconds")
            if status.session_close_beijing
            else None,
        }

    notifications = _recent_suggestions(project_root, settings)
    seen = {(item.get("type"), item.get("market"), item.get("symbol"), item.get("created_at")) for item in notifications}
    for event in _outbox_events(project_root, settings):
        key = (event.get("type"), event.get("market"), event.get("symbol"), event.get("created_at"))
        if key not in seen:
            notifications.append(event)
            seen.add(key)

    return {
        "generated_at": datetime.now(display_zone).isoformat(timespec="seconds"),
        "display_timezone": display_timezone,
        "markets": markets,
        "holdings": holdings,
        "focus_topics": configured_focus_topics(sources),
        "configuration": _model_configuration(settings),
        "notifications": notifications[:50],
        "briefs": _brief_files(project_root, settings),
        "quote_refresh": {
            "attempted": refresh_quotes,
            "failures": quote_refresh_failures,
        },
    }


def upsert_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    if market not in MARKETS:
        raise ValueError("market must be a_share or us_equities")
    new_value = normalize_holding(market, payload)
    ticker = new_value["ticker"]

    portfolios = sources.setdefault("portfolios", {})
    market_portfolio = portfolios.setdefault(market, {})
    holdings = market_portfolio.setdefault("holdings", [])
    if not isinstance(holdings, list):
        holdings = []
        market_portfolio["holdings"] = holdings

    for index, holding in enumerate(holdings):
        if str(holding.get("ticker", "")).strip().upper() == ticker:
            holdings[index] = new_value
            break
    else:
        holdings.append(new_value)

    _write_config_json(_sources_path(root, settings), sources)
    save_confirmed_snapshot(
        root,
        settings,
        market,
        holdings,
        source_type="web_config",
        source_ref=str(_sources_path(root, settings)),
        note="Saved from web dashboard.",
    )
    return new_value


def delete_holding(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    market = str(payload.get("market", "")).strip()
    ticker = str(payload.get("ticker", "")).strip().upper()
    if market not in MARKETS or not ticker:
        raise ValueError("market and ticker are required")
    holdings = sources.get("portfolios", {}).get(market, {}).get("holdings", [])
    if isinstance(holdings, list):
        sources["portfolios"][market]["holdings"] = [
            holding
            for holding in holdings
            if str(holding.get("ticker", "")).strip().upper() != ticker
        ]
    _write_config_json(_sources_path(root, settings), sources)
    save_confirmed_snapshot(
        root,
        settings,
        market,
        sources.get("portfolios", {}).get(market, {}).get("holdings", []),
        source_type="web_config",
        source_ref=str(_sources_path(root, settings)),
        note="Deleted from web dashboard.",
    )
    return {"market": market, "ticker": ticker, "deleted": True}


def upsert_focus_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    new_value = _normalized_topic(payload)

    topics = sources.setdefault("focus_topics", [])
    if not isinstance(topics, list):
        topics = []
        sources["focus_topics"] = topics

    for index, topic in enumerate(topics):
        if not isinstance(topic, Mapping):
            continue
        if str(topic.get("id", topic.get("name", ""))).strip() == new_value["id"]:
            topics[index] = new_value
            break
    else:
        topics.append(new_value)

    _write_config_json(_sources_path(root, settings), sources)
    return new_value


def delete_focus_topic(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = load_settings(root)
    sources = _read_sources(root, settings)
    topic_id = str(payload.get("id", "")).strip()
    if not topic_id:
        raise ValueError("topic id is required")
    topics = sources.get("focus_topics", [])
    if isinstance(topics, list):
        sources["focus_topics"] = [
            topic
            for topic in topics
            if not isinstance(topic, Mapping)
            or str(topic.get("id", topic.get("name", ""))).strip() != topic_id
        ]
    _write_config_json(_sources_path(root, settings), sources)
    return {"id": topic_id, "deleted": True}


class DashboardHandler(BaseHTTPRequestHandler):
    root: Path

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path)
            return
        if parsed.path == "/api/state":
            self._send_json(load_dashboard_state(self.root, refresh_quotes=True))
            return
        if parsed.path == "/events":
            self._send_events()
            return
        if parsed.path == "/briefs/" or parsed.path.startswith("/briefs/"):
            self._send_brief(parsed.path)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_static(self, path: str) -> None:
        relative = urllib.parse.unquote(path.removeprefix("/static/"))
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, content_type, target.read_bytes())

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/api/state":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/holdings":
                self._send_json(upsert_holding(self.root, payload))
                return
            if parsed.path == "/api/holdings/delete":
                self._send_json(delete_holding(self.root, payload))
                return
            if parsed.path == "/api/topics":
                self._send_json(upsert_focus_topic(self.root, payload))
                return
            if parsed.path == "/api/topics/delete":
                self._send_json(delete_focus_topic(self.root, payload))
                return
            if parsed.path == "/api/model-config":
                self._send_json(update_model_configuration(self.root, payload))
                return
            if parsed.path == "/api/feishu/portfolio-import":
                settings = load_settings(self.root)
                self._send_json(create_feishu_portfolio_import(self.root, settings, payload))
                return
            if parsed.path == "/api/feishu/portfolio-import/confirm":
                settings = load_settings(self.root)
                self._send_json(
                    confirm_feishu_portfolio_import(
                        self.root,
                        settings,
                        int(payload.get("id", 0)),
                    )
                )
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for _ in range(720):
            try:
                data = json.dumps(load_dashboard_state(self.root), ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(5)
            except (BrokenPipeError, ConnectionResetError):
                break

    def _send_brief(self, path: str) -> None:
        brief_root = (self.root / "briefs").resolve()
        if path == "/briefs/":
            body = "\n".join(
                f'<a href="{item["url"]}">{item["name"]}</a> '
                f'<span>{item["modified_at"].replace("T", " ")} {item["timezone"]}</span><br>'
                for item in _brief_files(self.root, load_settings(self.root), limit=200)
            )
            self._send_bytes(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                f"<!doctype html><meta charset='utf-8'><body>{body}</body>".encode("utf-8"),
            )
            return
        relative = urllib.parse.unquote(path.removeprefix("/briefs/"))
        candidate = (brief_root / relative).resolve()
        if not str(candidate).startswith(str(brief_root)) or not candidate.exists() or not candidate.is_file():
            self._send_json({"error": "brief not found"}, HTTPStatus.NOT_FOUND)
            return
        if candidate.suffix.lower() == ".md":
            markdown = candidate.read_text(encoding="utf-8")
            title = candidate.stem
            self._send_bytes(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                markdown_to_html(markdown, title=title).encode("utf-8"),
            )
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, content_type, candidate.read_bytes())


def run_web_server(host: str = "127.0.0.1", port: int = 8765, root: Path | None = None) -> None:
    project_root = root or find_project_root()

    class Handler(DashboardHandler):
        pass

    Handler.root = project_root
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Market Analyzer web dashboard: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Web dashboard stopped.", flush=True)
    finally:
        server.server_close()
