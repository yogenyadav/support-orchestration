"""db_state_read — read entity state from client DB (read-only, client-scoped).

Supports Oracle (primary), Postgres (some clients), MS SQL (WCS only).
Two-phase approach per §4.8:
  1. schema introspection — discover tables/columns from GitHub code or info_schema
  2. targeted read — query entity state once schema is known

External connection is behind DbAdapter; swap StubDbAdapter in tests.
Full tool registration in Prompt 3 (mcp_server.py build_mcp_server).
"""

from __future__ import annotations

from typing import Any

from .mcp_server import DbAdapter


async def db_state_read(
    client_id: str,
    entity_type: str,
    entity_id: str,
    db: DbAdapter,
    table_hint: str | None = None,
) -> dict[str, Any]:
    """
    Read the current state of a single entity from the client DB.

    Args:
        client_id:   Must match case.client — enforced by enforce_client_scope hook.
        entity_type: Logical entity name ("order", "tote", "bin").
        entity_id:   Entity primary key value.
        db:          Injected adapter (real or stub).
        table_hint:  Optional table name hint; if None, schema is introspected first.

    Returns dict with at minimum: {"entity_id": ..., "current_state": ..., "raw": {...}}
    """
    if table_hint is None:
        schema = await db.introspect_schema(client_id, entity_type)
        table_hint = schema.get("table_name", entity_type)

    # Parameterised query — never string-interpolated to prevent injection.
    rows = await db.query(
        client_id=client_id,
        sql=f"SELECT * FROM {table_hint} WHERE id = :entity_id",  # noqa: S608
        params={"entity_id": entity_id},
    )

    if not rows:
        return {"entity_id": entity_id, "current_state": None, "raw": {}, "found": False}

    row = rows[0]
    return {
        "entity_id": entity_id,
        "current_state": row.get("state") or row.get("status"),
        "raw": row,
        "found": True,
    }
