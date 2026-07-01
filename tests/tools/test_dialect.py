"""Tests for Teams dialect — one-open-request enforcement, verb validation."""

import pytest

from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case, Priority
from datetime import datetime, timedelta, timezone


def _make_case() -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        jira_ticket_id="WH-TEST-001",
        client="acme",
        assignee_email="eng@acme.com",
        priority=Priority.P2,
        created_at=now,
        sla_deadline=now + timedelta(hours=8),
    )


@pytest.mark.asyncio
async def test_send_prefixes_verb() -> None:
    transport = StubTransport()
    dm = DialectManager(transport, _make_case())
    dm.set_conversation_ref("conv-1")
    await dm.send("/info", "what is the current state of order 12345?")
    assert transport.sent[0].startswith("/info")


@pytest.mark.asyncio
async def test_invalid_verb_raises() -> None:
    transport = StubTransport()
    dm = DialectManager(transport, _make_case())
    dm.set_conversation_ref("conv-1")
    with pytest.raises(ValueError, match="Invalid dialect verb"):
        await dm.send("/unknown", "some message")


@pytest.mark.asyncio
async def test_double_send_raises_before_reply() -> None:
    transport = StubTransport(replies=["still prioritized"])
    dm = DialectManager(transport, _make_case())
    dm.set_conversation_ref("conv-1")
    await dm.send("/info", "first question")
    with pytest.raises(RuntimeError, match="open request already pending"):
        await dm.send("/ask", "second question before reply")


@pytest.mark.asyncio
async def test_receive_clears_open_request() -> None:
    case = _make_case()
    transport = StubTransport(replies=["still prioritized"])
    dm = DialectManager(transport, case)
    dm.set_conversation_ref("conv-1")
    await dm.send("/info", "what state is order 12345?")
    assert case.open_request is not None
    reply = await dm.receive()
    assert reply == "still prioritized"
    assert case.open_request is None
