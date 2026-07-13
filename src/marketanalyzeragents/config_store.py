from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")


def database_path(root: Path) -> Path:
    return root / "state" / "configuration.db"


def _connect(root: Path) -> sqlite3.Connection:
    path = database_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS configuration_documents (
            name TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def load_document(root: Path, name: str, seed_path: Path, default: Any) -> Any:
    with _connect(root) as connection:
        row = connection.execute(
            "SELECT payload FROM configuration_documents WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return json.loads(row[0])
        value = default
        if seed_path.exists():
            value = json.loads(seed_path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO configuration_documents(name, payload, updated_at) VALUES (?, ?, ?)",
            (
                name,
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                datetime.now(BEIJING).isoformat(timespec="seconds"),
            ),
        )
        return value


def save_document(root: Path, name: str, value: Mapping[str, Any]) -> None:
    payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
    updated_at = datetime.now(BEIJING).isoformat(timespec="seconds")
    with _connect(root) as connection:
        connection.execute(
            """
            INSERT INTO configuration_documents(name, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (name, payload, updated_at),
        )
