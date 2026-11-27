"""SQLite response cache with 24-hour TTL for token-lean research calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from forge.research.models import ResearchResult

DEFAULT_TTL_SECONDS = 86_400


class ResearchCache:
    """Local SQLite cache keyed by (provider, mode, max_tokens, query)."""

    def __init__(self, db_path: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_cache (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    max_tokens INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_epoch REAL NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def build_key(provider: str, query: str, mode: str, max_tokens: int) -> str:
        raw = f"{provider.lower()}|{mode.lower()}|{max_tokens}|{query.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(
        self,
        provider: str,
        query: str,
        mode: str,
        max_tokens: int,
        now_epoch: float | None = None,
    ) -> ResearchResult | None:
        """Return cached ResearchResult if present and within TTL."""
        key = self.build_key(provider, query, mode, max_tokens)
        now = time.time() if now_epoch is None else now_epoch
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, created_epoch FROM research_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        payload_json, created_epoch = row
        if now - float(created_epoch) > self.ttl_seconds:
            return None
        result = ResearchResult.model_validate(json.loads(payload_json))
        return result.model_copy(update={"cached": True})

    def set(
        self,
        result: ResearchResult,
        max_tokens: int,
        now_epoch: float | None = None,
    ) -> None:
        """Store or refresh a ResearchResult entry in SQLite."""
        key = self.build_key(result.provider, result.query, result.mode, max_tokens)
        now = time.time() if now_epoch is None else now_epoch
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_cache
                (cache_key, provider, query, mode, max_tokens, payload_json, created_epoch)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    result.provider,
                    result.query,
                    result.mode,
                    max_tokens,
                    result.model_dump_json(),
                    now,
                ),
            )
            conn.commit()
