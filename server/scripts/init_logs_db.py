"""
Initialize the PostgreSQL schema used by DatabaseLogService.

Usage:
    DATABASE_URL=postgresql://atc:atc@localhost:5432/atc_logs python scripts/init_logs_db.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
sys.path.insert(0, str(AGENTS_DIR))

from database_log_service import DEFAULT_DATABASE_URL, SCHEMA_SQL  # noqa: E402


def main() -> None:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    print("agent_logs schema is ready")


if __name__ == "__main__":
    main()
