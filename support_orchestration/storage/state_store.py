"""SQLite-backed Case state store.

Persists Case objects partitioned by client so an orchestrator can rehydrate
and resume after a crash (docs/4 §4.6.1). Schema is deliberately
Postgres-compatible — all types are standard SQL; migration from SQLite is a
straight CREATE TABLE copy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from support_orchestration.models import Case, CaseStatus

_DEFAULT_DB = Path(__file__).parents[2] / "state.db"

_TERMINAL_STATUSES: frozenset[CaseStatus] = frozenset({CaseStatus.resolved, CaseStatus.closed})


class CaseStore:
    """Thread-safe, append-update SQLite store for Case objects."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id        TEXT PRIMARY KEY,
                    jira_ticket_id TEXT NOT NULL,
                    client         TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    data           TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_jira
                    ON cases(jira_ticket_id);
                CREATE INDEX IF NOT EXISTS idx_cases_status
                    ON cases(status);
            """)

    def save_case(self, case: Case) -> None:
        """Upsert the full Case JSON. Thread-safe."""
        now = datetime.now(timezone.utc).isoformat()
        data = json.dumps(case.model_dump(mode="json"))
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO cases (case_id, jira_ticket_id, client, status, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    status     = excluded.status,
                    updated_at = excluded.updated_at,
                    data       = excluded.data
                """,
                (
                    case.case_id,
                    case.jira_ticket_id,
                    case.client,
                    case.status.value,
                    now,
                    data,
                ),
            )

    def load_case(self, case_id: str) -> Case | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return Case.model_validate(json.loads(row["data"]))

    def load_case_by_jira_id(self, jira_ticket_id: str) -> Case | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cases WHERE jira_ticket_id = ?", (jira_ticket_id,)
            ).fetchone()
        if row is None:
            return None
        return Case.model_validate(json.loads(row["data"]))

    def get_active_jira_ids(self) -> list[str]:
        """Return Jira ticket IDs for all non-terminal cases."""
        terminal = tuple(s.value for s in _TERMINAL_STATUSES)
        placeholders = ",".join("?" * len(terminal))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT jira_ticket_id FROM cases WHERE status NOT IN ({placeholders})",
                terminal,
            ).fetchall()
        return [r["jira_ticket_id"] for r in rows]

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn
