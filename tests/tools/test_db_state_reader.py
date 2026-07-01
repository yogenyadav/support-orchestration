"""Tests for db_state_read — entity state reads via stub adapter."""

import pytest

from support_orchestration.tools.db_state_reader import db_state_read
from support_orchestration.tools.mcp_server import StubDbAdapter


@pytest.mark.asyncio
async def test_returns_entity_state_when_found() -> None:
    fixture = [{"id": "12345", "state": "prioritized", "priority": 1}]
    result = await db_state_read("acme", "order", "12345", StubDbAdapter(fixture))
    assert result["found"] is True
    assert result["current_state"] == "prioritized"
    assert result["entity_id"] == "12345"


@pytest.mark.asyncio
async def test_returns_not_found_when_empty() -> None:
    result = await db_state_read("acme", "order", "99999", StubDbAdapter([]))
    assert result["found"] is False
    assert result["current_state"] is None


@pytest.mark.asyncio
async def test_uses_status_field_as_fallback() -> None:
    fixture = [{"id": "T-01", "status": "picking_complete"}]
    result = await db_state_read("acme", "tote", "T-01", StubDbAdapter(fixture))
    assert result["current_state"] == "picking_complete"
