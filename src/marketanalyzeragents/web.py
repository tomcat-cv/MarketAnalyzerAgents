from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .analysis_system import (
    beijing_now,
    dashboard_state,
    delete_holding,
    delete_topic,
    generate_intraday_suggestion,
    generate_market_report,
    service_loop,
    update_model_configuration,
    update_source_configuration,
    upsert_holding,
    upsert_topic,
)
from .config import find_project_root, load_settings, resolve_path


STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


class ReportRunState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {"state": "idle"}
        self._started_monotonic: float | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
            if status.get("state") == "running" and self._started_monotonic is not None:
                status["elapsed_seconds"] = max(0, int(time.monotonic() - self._started_monotonic))
            return status

    def start(self, root: Path, backend: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._status.get("state") == "running":
                return self.snapshot()
            settings = load_settings(root)
            started_at = beijing_now(settings).isoformat(timespec="seconds")
            self._status = {
                "state": "running",
                "started_at": started_at,
                "message": "正在收集资讯、行情和市场情绪",
                "elapsed_seconds": 0,
            }
            self._started_monotonic = time.monotonic()
        threading.Thread(
            target=self._run,
            kwargs={"root": root, "backend": backend},
            daemon=True,
            name="market-analyzer-manual-report",
        ).start()
        return self.snapshot()

    def _run(self, root: Path, backend: str | None) -> None:
        try:
            report = generate_market_report(root, backend=backend)
        except Exception as exc:
            settings = load_settings(root)
            with self._lock:
                self._status = {
                    "state": "failed",
                    "started_at": self._status.get("started_at"),
                    "completed_at": beijing_now(settings).isoformat(timespec="seconds"),
                    "elapsed_seconds": self._elapsed_seconds(),
                    "error": str(exc),
                }
                self._started_monotonic = None
            return
        settings = load_settings(root)
        with self._lock:
            self._status = {
                "state": "completed",
                "started_at": self._status.get("started_at"),
                "completed_at": beijing_now(settings).isoformat(timespec="seconds"),
                "elapsed_seconds": self._elapsed_seconds(),
                "result": {
                    "id": report.get("id"),
                    "title": report.get("title"),
                    "generated_at": report.get("generated_at"),
                    "official_count": report.get("official_count"),
                    "social_count": report.get("social_count"),
                },
            }
            self._started_monotonic = None

    def _elapsed_seconds(self) -> int:
        if self._started_monotonic is None:
            return 0
        return max(0, int(time.monotonic() - self._started_monotonic))


REPORT_RUN_STATE = ReportRunState()


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

    def _send_head(self, status: HTTPStatus, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_file(self, path: Path, fallback_type: str = "application/octet-stream") -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or fallback_type
        self._send_bytes(HTTPStatus.OK, content_type, path.read_bytes())

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if self.path == "/api/state":
            self._send_json(dashboard_state(self.root))
            return
        if self.path == "/api/report/status":
            self._send_json(REPORT_RUN_STATE.snapshot())
            return
        if self.path.startswith("/static/"):
            self._send_file(STATIC_DIR / self.path.removeprefix("/static/"))
            return
        if self.path.startswith("/analysis/reports/"):
            settings = load_settings(self.root)
            directory = resolve_path(self.root, settings.get("state", {}).get("analysis_dir", "state/analysis")) / "reports"
            self._send_file(directory / self.path.removeprefix("/analysis/reports/"), "text/html; charset=utf-8")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_head(HTTPStatus.OK, "text/html; charset=utf-8", len(INDEX_HTML.encode("utf-8")))
            return
        self._send_head(HTTPStatus.NOT_FOUND, "application/json; charset=utf-8", 0)

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/api/model-config":
                self._send_json(update_model_configuration(self.root, payload))
            elif self.path == "/api/sources":
                self._send_json(update_source_configuration(self.root, payload))
            elif self.path == "/api/holdings":
                self._send_json(upsert_holding(self.root, payload))
            elif self.path == "/api/holdings/delete":
                self._send_json(delete_holding(self.root, payload))
            elif self.path == "/api/topics":
                self._send_json(upsert_topic(self.root, payload))
            elif self.path == "/api/topics/delete":
                self._send_json(delete_topic(self.root, payload))
            elif self.path == "/api/report/run":
                self._send_json(REPORT_RUN_STATE.start(self.root, backend=payload.get("backend")), HTTPStatus.ACCEPTED)
            elif self.path == "/api/suggestion/run":
                self._send_json(generate_intraday_suggestion(self.root, backend=payload.get("backend")))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def run_web_server(host: str = "127.0.0.1", port: int = 8765, root: Path | None = None) -> None:
    project_root = root or find_project_root()
    if os.environ.get("MARKET_ANALYZER_AGENTS_WEB_SERVICE", "1").strip().lower() not in {"0", "false", "no"}:
        threading.Thread(
            target=service_loop,
            kwargs={"root": project_root},
            daemon=True,
            name="market-analyzer-scheduler",
        ).start()
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"root": project_root})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Market Analyzer web running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
