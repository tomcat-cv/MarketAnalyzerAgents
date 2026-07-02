from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Quote:
    market: str
    symbol: str
    observed_at: str
    price: float
    previous_close: float | None = None
    volume: float | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class PriceBar:
    market: str
    symbol: str
    interval: str
    observed_at: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    source: str = "unknown"


class PortfolioStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            PRAGMA user_version = 1;
            CREATE TABLE IF NOT EXISTS quotes (
              market TEXT NOT NULL, symbol TEXT NOT NULL, observed_at TEXT NOT NULL,
              price REAL NOT NULL, previous_close REAL, volume REAL, source TEXT NOT NULL,
              PRIMARY KEY (market, symbol, observed_at)
            );
            CREATE TABLE IF NOT EXISTS price_bars (
              market TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
              observed_at TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
              low REAL NOT NULL, close REAL NOT NULL, volume REAL, source TEXT NOT NULL,
              PRIMARY KEY (market, symbol, interval, observed_at)
            );
            CREATE TABLE IF NOT EXISTS suggestions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL,
              symbol TEXT NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL,
              confidence TEXT NOT NULL, rationale TEXT NOT NULL,
              evidence_json TEXT NOT NULL, invalidation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
              id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL,
              symbol TEXT NOT NULL, operated_at TEXT NOT NULL, action TEXT NOT NULL,
              quantity REAL NOT NULL, price REAL NOT NULL, note TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_discussions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, suggestion_id INTEGER NOT NULL,
              turn_index INTEGER NOT NULL, role TEXT NOT NULL,
              round_number INTEGER NOT NULL, content TEXT NOT NULL,
              FOREIGN KEY (suggestion_id) REFERENCES suggestions(id)
            );
            CREATE TABLE IF NOT EXISTS evidence_packs (
              retrieved_at TEXT PRIMARY KEY, window_start TEXT NOT NULL,
              window_end TEXT NOT NULL, window_mode TEXT NOT NULL,
              timezone TEXT NOT NULL, lookback_hours INTEGER NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_items (
              retrieved_at TEXT NOT NULL, evidence_id TEXT NOT NULL,
              title TEXT NOT NULL, published_at TEXT NOT NULL,
              source_name TEXT NOT NULL, source_type TEXT NOT NULL,
              url TEXT NOT NULL, display_url TEXT NOT NULL,
              content TEXT NOT NULL, evidence_level TEXT NOT NULL,
              matched_topics_json TEXT NOT NULL, matched_tickers_json TEXT NOT NULL,
              PRIMARY KEY (retrieved_at, evidence_id),
              FOREIGN KEY (retrieved_at) REFERENCES evidence_packs(retrieved_at) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_quotes_recent
              ON quotes (market, symbol, observed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_price_bars_recent
              ON price_bars (market, symbol, interval, observed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_lookup
              ON suggestions (market, symbol, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operations_review
              ON operations (market, operated_at);
            CREATE INDEX IF NOT EXISTS idx_agent_discussions_suggestion
              ON agent_discussions (suggestion_id, turn_index);
            CREATE INDEX IF NOT EXISTS idx_evidence_items_recent
              ON evidence_items (evidence_level, published_at DESC);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PortfolioStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_quotes(self, quotes: Iterable[Quote]) -> None:
        self.connection.executemany(
            """INSERT OR REPLACE INTO quotes
               (market,symbol,observed_at,price,previous_close,volume,source)
               VALUES (:market,:symbol,:observed_at,:price,:previous_close,:volume,:source)""",
            [asdict(quote) for quote in quotes],
        )
        self.connection.commit()

    def recent_quotes(self, market: str, symbol: str, limit: int = 2) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM quotes WHERE market=? AND symbol=?
               ORDER BY observed_at DESC LIMIT ?""",
            (market, symbol, limit),
        ).fetchall()

    def save_price_bars(self, bars: Iterable[PriceBar]) -> None:
        self.connection.executemany(
            """INSERT OR REPLACE INTO price_bars
               (market,symbol,interval,observed_at,open,high,low,close,volume,source)
               VALUES (:market,:symbol,:interval,:observed_at,:open,:high,:low,:close,:volume,:source)""",
            [asdict(bar) for bar in bars],
        )
        self.connection.commit()

    def recent_price_bars(
        self, market: str, symbol: str, interval: str, limit: int = 20
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM price_bars
               WHERE market=? AND symbol=? AND interval=?
               ORDER BY observed_at DESC LIMIT ?""",
            (market, symbol, interval, limit),
        ).fetchall()

    def save_suggestion(self, suggestion: dict[str, Any]) -> int:
        cursor = self.connection.execute(
            """INSERT INTO suggestions
               (market,symbol,created_at,action,confidence,rationale,evidence_json,invalidation)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                suggestion["market"], suggestion["symbol"], suggestion["created_at"],
                suggestion["action"], suggestion["confidence"], suggestion["rationale"],
                json.dumps(suggestion.get("evidence_ids", []), ensure_ascii=False),
                suggestion["invalidation"],
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save_operation(self, operation: dict[str, Any]) -> int:
        cursor = self.connection.execute(
            """INSERT INTO operations
               (market,symbol,operated_at,action,quantity,price,note)
               VALUES (?,?,?,?,?,?,?)""",
            tuple(operation[key] for key in (
                "market", "symbol", "operated_at", "action", "quantity", "price", "note"
            )),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save_discussion(self, suggestion_id: int, turns: Iterable[Any]) -> None:
        self.connection.executemany(
            """INSERT INTO agent_discussions
               (suggestion_id,turn_index,role,round_number,content)
               VALUES (?,?,?,?,?)""",
            [
                (suggestion_id, index, turn.role, turn.round, turn.content)
                for index, turn in enumerate(turns)
            ],
        )
        self.connection.commit()

    def save_evidence_pack(self, pack: Mapping[str, Any]) -> None:
        retrieved_at = str(pack.get("retrieved_at", ""))
        if not retrieved_at:
            raise ValueError("Evidence pack must include retrieved_at.")
        self.connection.execute(
            """INSERT OR REPLACE INTO evidence_packs
               (retrieved_at,window_start,window_end,window_mode,timezone,lookback_hours,payload_json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                retrieved_at,
                str(pack.get("window_start", "")),
                str(pack.get("window_end", "")),
                str(pack.get("window_mode", "")),
                str(pack.get("timezone", "")),
                int(pack.get("lookback_hours", 0)),
                json.dumps(pack, ensure_ascii=False),
            ),
        )
        self.connection.execute("DELETE FROM evidence_items WHERE retrieved_at=?", (retrieved_at,))
        rows = []
        for item in pack.get("items", []):
            if not isinstance(item, Mapping):
                continue
            rows.append(
                (
                    retrieved_at,
                    str(item.get("id", "")),
                    str(item.get("title", "")),
                    str(item.get("published_at", "")),
                    str(item.get("source_name", "")),
                    str(item.get("source_type", "")),
                    str(item.get("url", "")),
                    str(item.get("display_url", "")),
                    str(item.get("content", "")),
                    str(item.get("evidence_level", "")),
                    json.dumps(item.get("matched_topics", []), ensure_ascii=False),
                    json.dumps(item.get("matched_tickers", []), ensure_ascii=False),
                )
            )
        self.connection.executemany(
            """INSERT OR REPLACE INTO evidence_items
               (retrieved_at,evidence_id,title,published_at,source_name,source_type,url,display_url,
                content,evidence_level,matched_topics_json,matched_tickers_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.connection.commit()

    def recent_summary_evidence_for_tickers(
        self,
        tickers: Sequence[str],
        *,
        since: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, list[dict[str, Any]]]:
        wanted = {ticker.upper().strip() for ticker in tickers if ticker.strip()}
        if not wanted:
            return {}
        rows = self.connection.execute(
            """SELECT * FROM evidence_items WHERE evidence_level='summary'
               ORDER BY published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in wanted}
        for row in rows:
            try:
                published_at = datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
            except ValueError:
                published_at = None
            if since is not None and published_at is not None and published_at < since:
                continue
            try:
                matched_tickers = json.loads(row["matched_tickers_json"])
            except json.JSONDecodeError:
                matched_tickers = []
            try:
                matched_topics = json.loads(row["matched_topics_json"])
            except json.JSONDecodeError:
                matched_topics = []
            item = {
                "id": row["evidence_id"],
                "title": row["title"],
                "published_at": row["published_at"],
                "source_name": row["source_name"],
                "source_type": row["source_type"],
                "url": row["url"],
                "display_url": row["display_url"],
                "content": row["content"],
                "evidence_level": row["evidence_level"],
                "matched_topics": matched_topics,
                "matched_tickers": matched_tickers,
            }
            for ticker in {str(value).upper().strip() for value in matched_tickers} & wanted:
                grouped.setdefault(ticker, []).append(item)
        return grouped

    def discussion_for_suggestion(self, suggestion_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM agent_discussions WHERE suggestion_id=?
               ORDER BY turn_index""",
            (suggestion_id,),
        ).fetchall()

    def prune_market_data(self, retention_days: int, now: datetime | None = None) -> dict[str, int]:
        if retention_days <= 0:
            return {"quotes": 0, "price_bars": 0, "evidence_packs": 0}
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
        cutoff_text = cutoff.isoformat()
        quote_cursor = self.connection.execute(
            "DELETE FROM quotes WHERE observed_at < ?",
            (cutoff_text,),
        )
        bar_cursor = self.connection.execute(
            "DELETE FROM price_bars WHERE observed_at < ?",
            (cutoff_text,),
        )
        self.connection.execute(
            "DELETE FROM evidence_items WHERE retrieved_at < ?",
            (cutoff_text,),
        )
        evidence_cursor = self.connection.execute(
            "DELETE FROM evidence_packs WHERE retrieved_at < ?",
            (cutoff_text,),
        )
        self.connection.commit()
        return {
            "quotes": int(quote_cursor.rowcount if quote_cursor.rowcount is not None else 0),
            "price_bars": int(bar_cursor.rowcount if bar_cursor.rowcount is not None else 0),
            "evidence_packs": int(evidence_cursor.rowcount if evidence_cursor.rowcount is not None else 0),
        }

    def operations_for_date(self, market: str, day: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM operations WHERE market=? AND substr(operated_at,1,10)=?
               ORDER BY operated_at""",
            (market, day),
        ).fetchall()

    def latest_suggestion_before(self, market: str, symbol: str, timestamp: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT * FROM suggestions WHERE market=? AND symbol=? AND created_at<=?
               ORDER BY created_at DESC LIMIT 1""",
            (market, symbol, timestamp),
        ).fetchone()
