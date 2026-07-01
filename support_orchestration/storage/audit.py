"""Append-only audit store for tool reads and human approvals.

Backed by SQLite for the PoC (zero-infra, in-process).
Schema uses no SQLite-specific types so migration to Postgres in Prompt 5 is a
straight CREATE TABLE copy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DB = Path(__file__).parents[2] / "audit.db"


class AuditStore:
    """Thread-safe append-only store with two tables: audit_reads, audit_approvals."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_reads (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT    NOT NULL,
                    case_id       TEXT    NOT NULL,
                    client        TEXT    NOT NULL,
                    tool_name     TEXT    NOT NULL,
                    input_keys    TEXT    NOT NULL,
                    output_size   INTEGER NOT NULL DEFAULT 0,
                    credential_id TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_approvals (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        TEXT    NOT NULL,
                    case_id          TEXT    NOT NULL,
                    client           TEXT    NOT NULL,
                    verb             TEXT    NOT NULL,
                    message          TEXT    NOT NULL,
                    mirrored_to_jira INTEGER NOT NULL DEFAULT 0
                );
            """)

    def append_read(
        self,
        *,
        case_id: str,
        client: str,
        tool_name: str,
        input_keys: list[str],
        output_size: int = 0,
        credential_id: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_reads"
                " (timestamp, case_id, client, tool_name, input_keys, output_size, credential_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    case_id, client, tool_name,
                    json.dumps(input_keys),
                    output_size,
                    credential_id,
                ),
            )

    def append_approval(
        self,
        *,
        case_id: str,
        client: str,
        verb: str,
        message: str,
        mirrored_to_jira: bool = False,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_approvals"
                " (timestamp, case_id, client, verb, message, mirrored_to_jira)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    case_id, client, verb, message,
                    int(mirrored_to_jira),
                ),
            )

    def reads_for_case(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_reads WHERE case_id = ? ORDER BY id",
                (case_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def approvals_for_case(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_approvals WHERE case_id = ? ORDER BY id",
                (case_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn
