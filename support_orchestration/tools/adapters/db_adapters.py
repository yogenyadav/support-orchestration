"""Production DB adapters — Oracle (primary), Postgres, MS SQL.

Credentials flow: Phoenix resolves the connectivity tier; the orchestrator
reads connection details from the secrets vault and passes them to the
adapter constructor. Connection details are never hardcoded here.

Each adapter enforces client_id — if a query arrives for a different client
it raises PermissionError (belt-and-suspenders alongside the PreToolUse hook).

SQL parameter format in the DbAdapter ABC uses Oracle-style :name placeholders.
Each adapter converts to its native format before executing.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from support_orchestration.tools.mcp_server import DbAdapter


def _named_to_positional(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Convert :name → $n (Postgres asyncpg style)."""
    values: list[Any] = []
    counter = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        values.append(params[m.group(1)])
        return f"${counter}"

    return re.sub(r":(\w+)", _replace, sql), values


def _named_to_qmark(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Convert :name → ? (pyodbc style). Preserves order of first occurrence."""
    values: list[Any] = []

    def _replace(m: re.Match[str]) -> str:
        values.append(params[m.group(1)])
        return "?"

    return re.sub(r":(\w+)", _replace, sql), values


class _ScopedMixin:
    """Shared client-scope enforcement."""

    _client_id: str

    def _check_scope(self, client_id: str) -> None:
        if client_id != self._client_id:
            raise PermissionError(
                f"DB adapter scope violation: request for client '{client_id}' "
                f"arrived at adapter bound to '{self._client_id}'."
            )


class OracleDbAdapter(_ScopedMixin, DbAdapter):
    """
    Read-only Oracle adapter (primary client DB engine).

    Uses oracledb thin-mode — no Oracle Instant Client required.
    Install: pip install oracledb

    Args:
        client_id:    Client this adapter is bound to.
        host:         Oracle DB hostname or IP.
        port:         Oracle listener port (default 1521).
        service_name: Oracle service name (preferred over SID).
        user:         Read-only DB user.
        password:     Password from secrets vault.
    """

    def __init__(
        self,
        client_id: str,
        host: str,
        port: int,
        service_name: str,
        user: str,
        password: str,
    ) -> None:
        try:
            import oracledb  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "oracledb is required for Oracle DB access. Install: pip install oracledb"
            ) from exc

        self._client_id = client_id
        self._host = host
        self._port = port
        self._service_name = service_name
        self._user = user
        self._password = password

    def _connect(self) -> Any:
        import oracledb
        return oracledb.connect(
            user=self._user,
            password=self._password,
            host=self._host,
            port=self._port,
            service_name=self._service_name,
        )

    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self._check_scope(client_id)

        def _sync() -> list[dict[str, Any]]:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)  # oracledb supports :name natively
                    cols = [d[0].lower() for d in (cur.description or [])]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]

        return await asyncio.to_thread(_sync)

    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        self._check_scope(client_id)
        hint = table_hint.upper()

        def _sync() -> dict[str, Any]:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT table_name, column_name, data_type "
                        "FROM all_tab_columns "
                        "WHERE UPPER(table_name) LIKE :hint "
                        "ORDER BY table_name, column_id",
                        {"hint": f"%{hint}%"},
                    )
                    rows = cur.fetchall()
                    if not rows:
                        return {"table_name": table_hint, "columns": []}
                    actual = rows[0][0]
                    return {
                        "table_name": actual,
                        "columns": [
                            {"name": r[1].lower(), "type": r[2]} for r in rows
                            if r[0] == actual
                        ],
                    }

        return await asyncio.to_thread(_sync)


class PostgresDbAdapter(_ScopedMixin, DbAdapter):
    """
    Read-only Postgres adapter (some clients use Postgres instead of Oracle).

    Uses asyncpg connection pool for efficient async access.
    Install: pip install asyncpg

    Args:
        client_id: Client this adapter is bound to.
        dsn:       asyncpg DSN string: postgresql://user:pass@host:port/db
    """

    def __init__(self, client_id: str, dsn: str) -> None:
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is required for Postgres DB access. Install: pip install asyncpg"
            ) from exc

        self._client_id = client_id
        self._dsn = dsn
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5, command_timeout=30)
        return self._pool

    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self._check_scope(client_id)
        converted_sql, values = _named_to_positional(sql, params)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(converted_sql, *values)
            return [dict(r) for r in rows]

    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        self._check_scope(client_id)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.table_name, c.column_name, c.data_type
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_name = c.table_name AND t.table_schema = c.table_schema
                WHERE c.table_schema = 'public'
                  AND c.table_name ILIKE $1
                ORDER BY c.ordinal_position
                """,
                f"%{table_hint}%",
            )
            if not rows:
                return {"table_name": table_hint, "columns": []}
            actual = rows[0]["table_name"]
            return {
                "table_name": actual,
                "columns": [
                    {"name": r["column_name"], "type": r["data_type"]}
                    for r in rows if r["table_name"] == actual
                ],
            }


class MsSqlDbAdapter(_ScopedMixin, DbAdapter):
    """
    Read-only MS SQL adapter (used by WCS domain clients only).

    Uses pyodbc with ODBC Driver 17/18 for SQL Server.
    Install: pip install pyodbc  (plus the OS-level ODBC driver)

    Args:
        client_id: Client this adapter is bound to.
        server:    SQL Server host[:port].
        database:  Target database name.
        user:      Read-only SQL user.
        password:  Password from secrets vault.
    """

    def __init__(
        self,
        client_id: str,
        server: str,
        database: str,
        user: str,
        password: str,
    ) -> None:
        try:
            import pyodbc  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "pyodbc is required for MS SQL access. Install: pip install pyodbc"
            ) from exc

        self._client_id = client_id
        self._conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            f"SERVER={server};DATABASE={database};"
            f"UID={user};PWD={password};"
            "ReadOnly=1"
        )

    def _connect(self) -> Any:
        import pyodbc
        return pyodbc.connect(self._conn_str, autocommit=True)

    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self._check_scope(client_id)
        converted_sql, values = _named_to_qmark(sql, params)

        def _sync() -> list[dict[str, Any]]:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(converted_sql, values)
                cols = [d[0].lower() for d in (cur.description or [])]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

        return await asyncio.to_thread(_sync)

    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        self._check_scope(client_id)

        def _sync() -> dict[str, Any]:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT t.name AS table_name, c.name AS column_name, tp.name AS data_type
                    FROM sys.columns c
                    JOIN sys.tables t ON t.object_id = c.object_id
                    JOIN sys.types tp ON tp.user_type_id = c.user_type_id
                    WHERE t.name LIKE ?
                    ORDER BY t.name, c.column_id
                    """,
                    [f"%{table_hint}%"],
                )
                rows = cur.fetchall()
                if not rows:
                    return {"table_name": table_hint, "columns": []}
                actual = rows[0][0]
                return {
                    "table_name": actual,
                    "columns": [
                        {"name": r[1], "type": r[2]} for r in rows if r[0] == actual
                    ],
                }

        return await asyncio.to_thread(_sync)
