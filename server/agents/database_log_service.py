"""
database_log_service.py — PostgreSQL-backed event log for Ray ATC agents.

The service stores structured communication and decision events emitted by
voivodeship actors.  Keeping writes behind one Ray actor makes database access
simple for the rest of the actor network and gives the API a single place to
query historical logs.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import ray

from voivodeship_keys import expand_voivodeship_filter, normalize_voivodeship_key

logger = logging.getLogger("ATC.DatabaseLogService")


DEFAULT_DATABASE_URL = "postgresql://atc:atc@postgres:5432/atc_logs"


@ray.remote
class DatabaseLogService:
    """Stores and queries structured ATC agent logs in PostgreSQL."""

    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self._enabled = True
        self._last_error: Optional[str] = None
        self._ensure_schema()

    def record(
        self,
        *,
        event_type: str,
        message: str,
        tick: Optional[int] = None,
        sim_time: Optional[float] = None,
        source_voivodeship: Optional[str] = None,
        target_voivodeship: Optional[str] = None,
        flight_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist one structured log event."""
        if not self._enabled:
            return False
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_logs (
                            tick, sim_time, source_voivodeship, target_voivodeship,
                            event_type, flight_id, message, payload
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            tick,
                            sim_time,
                            normalize_voivodeship_key(source_voivodeship),
                            normalize_voivodeship_key(target_voivodeship),
                            event_type,
                            flight_id,
                            message,
                            json.dumps(payload or {}),
                        ),
                    )
            self._last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("[DatabaseLogService] Could not store log: %s", exc)
            return False

    def query(
        self,
        *,
        source_voivodeship: Optional[str] = None,
        source_voivodeships: Optional[Sequence[str]] = None,
        target_voivodeship: Optional[str] = None,
        target_voivodeships: Optional[Sequence[str]] = None,
        event_type: Optional[str] = None,
        event_types: Optional[Sequence[str]] = None,
        flight_id: Optional[str] = None,
        flight_ids: Optional[Sequence[str]] = None,
        text: Optional[str] = None,
        tick_from: Optional[int] = None,
        tick_to: Optional[int] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Return persisted logs matching optional filters."""
        if not self._enabled:
            return []

        limit = max(1, min(int(limit or 100), 500))
        clauses: List[str] = []
        params: List[Any] = []

        selected_sources = expand_voivodeship_filter(
            [value for value in (source_voivodeships or []) if value]
        )
        if selected_sources:
            clauses.append("source_voivodeship = ANY(%s)")
            params.append(selected_sources)
        elif source_voivodeship:
            clauses.append("source_voivodeship = ANY(%s)")
            params.append(expand_voivodeship_filter([source_voivodeship]))

        selected_targets = expand_voivodeship_filter(
            [value for value in (target_voivodeships or []) if value]
        )
        if selected_targets:
            clauses.append("target_voivodeship = ANY(%s)")
            params.append(selected_targets)
        elif target_voivodeship:
            clauses.append("target_voivodeship = ANY(%s)")
            params.append(expand_voivodeship_filter([target_voivodeship]))

        selected_event_types = [value for value in (event_types or []) if value]
        if selected_event_types:
            clauses.append("event_type = ANY(%s)")
            params.append(selected_event_types)
        elif event_type:
            clauses.append("event_type = %s")
            params.append(event_type)

        selected_flight_ids = [value for value in (flight_ids or []) if value]
        if selected_flight_ids:
            clauses.append("flight_id = ANY(%s)")
            params.append(selected_flight_ids)
        elif flight_id:
            clauses.append("flight_id = %s")
            params.append(flight_id)
        if text:
            clauses.append("message ILIKE %s")
            params.append(f"%{text}%")
        if tick_from is not None:
            clauses.append("tick >= %s")
            params.append(tick_from)
        if tick_to is not None:
            clauses.append("tick <= %s")
            params.append(tick_to)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT
                            id, created_at, tick, sim_time, source_voivodeship,
                            target_voivodeship, event_type, flight_id, message, payload
                        FROM agent_logs
                        {where_sql}
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        params,
                    )
                    rows = cur.fetchall()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("[DatabaseLogService] Could not query logs: %s", exc)
            return []

        return [self._row_to_dict(row) for row in rows]

    def get_event_types(self) -> List[str]:
        """Return event types currently present in the database."""
        if not self._enabled:
            return []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT event_type FROM agent_logs ORDER BY event_type")
                    return [row[0] for row in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("[DatabaseLogService] Could not list event types: %s", exc)
            return []

    def get_filter_options(self) -> dict:
        """Return distinct values used by the history filter dropdowns."""
        if not self._enabled:
            return {"sources": [], "targets": [], "event_types": [], "flight_ids": []}
        queries = {
            "sources": "SELECT DISTINCT source_voivodeship FROM agent_logs WHERE source_voivodeship IS NOT NULL ORDER BY source_voivodeship LIMIT 500",
            "targets": "SELECT DISTINCT target_voivodeship FROM agent_logs WHERE target_voivodeship IS NOT NULL ORDER BY target_voivodeship LIMIT 500",
            "event_types": "SELECT DISTINCT event_type FROM agent_logs WHERE event_type IS NOT NULL ORDER BY event_type LIMIT 500",
            "flight_ids": "SELECT DISTINCT flight_id FROM agent_logs WHERE flight_id IS NOT NULL ORDER BY flight_id LIMIT 500",
        }
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    result = {}
                    for key, sql in queries.items():
                        cur.execute(sql)
                        if key in {"sources", "targets"}:
                            result[key] = self._dedupe_voivodeship_values(
                                row[0] for row in cur.fetchall()
                            )
                        else:
                            result[key] = [row[0] for row in cur.fetchall()]
                    return result
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("[DatabaseLogService] Could not list filter options: %s", exc)
            return {"sources": [], "targets": [], "event_types": [], "flight_ids": []}

    def status(self) -> dict:
        """Return basic health information for diagnostics."""
        return {
            "enabled": self._enabled,
            "database_url": self._redact_database_url(self.database_url),
            "last_error": self._last_error,
        }

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA_SQL)
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("[DatabaseLogService] Schema initialization failed: %s", exc)

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    @staticmethod
    def _row_to_dict(row) -> dict:
        payload = row[9] or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        created_at = row[1]
        if isinstance(created_at, datetime.datetime):
            created_at = created_at.isoformat()
        return {
            "id": row[0],
            "created_at": created_at,
            "tick": row[2],
            "sim_time": row[3],
            "source_voivodeship": normalize_voivodeship_key(row[4]),
            "target_voivodeship": normalize_voivodeship_key(row[5]),
            "event_type": row[6],
            "flight_id": row[7],
            "message": row[8],
            "payload": payload,
        }

    @staticmethod
    def _dedupe_voivodeship_values(values) -> List[str]:
        seen = set()
        normalized: List[str] = []
        for value in values:
            key = normalize_voivodeship_key(value)
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized

    @staticmethod
    def _redact_database_url(database_url: str) -> str:
        if "@" not in database_url or "://" not in database_url:
            return database_url
        prefix, rest = database_url.split("://", 1)
        return f"{prefix}://***@{rest.split('@', 1)[1]}"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tick INTEGER,
    sim_time DOUBLE PRECISION,
    source_voivodeship TEXT,
    target_voivodeship TEXT,
    event_type TEXT NOT NULL,
    flight_id TEXT,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_tick ON agent_logs (tick DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_source ON agent_logs (source_voivodeship);
CREATE INDEX IF NOT EXISTS idx_agent_logs_target ON agent_logs (target_voivodeship);
CREATE INDEX IF NOT EXISTS idx_agent_logs_event_type ON agent_logs (event_type);
CREATE INDEX IF NOT EXISTS idx_agent_logs_flight_id ON agent_logs (flight_id);
"""
