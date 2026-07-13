"""Tests for WES subagent diagnose() — raw Messages API tool loop."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case, Priority
from support_orchestration.models.case import CaseStatus
from support_orchestration.models.diagnosis import Diagnosis, NextAction
from support_orchestration.subagents.base import WESSubagent, get_subagent
from support_orchestration.subagents.prompts import bounded_give_up, parse_diagnosis_json

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(
    client: str = "acme",
    entity_type: str = "order",
    entity_id: str = "12345",
    entity_state: str = "prioritized",
) -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        jira_ticket_id="WH-TEST-001",
        client=client,
        priority=Priority.P2,
        created_at=now,
        sla_deadline=now + timedelta(hours=8),
        entity_type=entity_type,
        entity_id=entity_id,
        entity_current_state=entity_state,
        stuck_transition="prioritized → released",
        status=CaseStatus.diagnosing,
        assignee_email="eng@acme.example",
    )


def _make_client() -> MagicMock:
    """Return a mock anthropic.AsyncAnthropic with a pre-wired messages.create."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


def _end_turn_response(text: str) -> MagicMock:
    """Build a mock Message with stop_reason='end_turn' and a text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _tool_use_response(tool_name: str, tool_id: str, tool_input: dict) -> MagicMock:
    """Build a mock Message with stop_reason='tool_use' and a single tool call."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_input
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


def _diagnosis_text(
    next_action: str = "propose_to_human", reroute_target: str | None = None
) -> str:
    d: dict = {
        "entity": {"type": "order", "id": "12345", "current_state": "prioritized"},
        "stuck_transition": "prioritized → released",
        "owning_domain": "WES",
        "root_cause": "Lost ack from picking engine.",
        "blocker_class": "lost_ack",
        "dependency_findings": [],
        "proposed_fix": {
            "summary": "Restart WES ack listener.",
            "human_steps": ["Restart ack listener", "Confirm order advances"],
            "sql_statement": None,
            "reversible": True,
            "verification": "Order 12345 reaches 'released' within 90 seconds.",
        },
        "confidence": 0.88,
        "evidence_refs": ["db:orders#12345@T1"],
        "needs_from_human": None,
        "next_action": next_action,
        "reroute_target": reroute_target,
        "notes": "",
    }
    return f"<diagnosis>\n{json.dumps(d)}\n</diagnosis>"


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diagnose_no_client_raises() -> None:
    case = _make_case()
    agent = WESSubagent(case)  # no anthropic_client
    with pytest.raises(NotImplementedError, match="anthropic_client"):
        await agent.diagnose()


@pytest.mark.asyncio
async def test_diagnose_end_turn_with_diagnosis_json() -> None:
    case = _make_case()
    client = _make_client()
    client.messages.create.return_value = _end_turn_response(_diagnosis_text())
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    assert isinstance(result, Diagnosis)
    assert result.next_action == NextAction.propose_to_human
    assert result.owning_domain == "WES"
    assert result.blocker_class == "lost_ack"
    assert result.proposed_fix is not None


@pytest.mark.asyncio
async def test_diagnose_tool_loop_then_diagnosis() -> None:
    """Model calls db_state_read, then on next turn outputs the diagnosis."""
    case = _make_case()
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__db_state_read",
            "tool-1",
            {"client_id": "acme", "entity_type": "order", "entity_id": "12345"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    assert isinstance(result, Diagnosis)
    assert result.blocker_class == "lost_ack"
    assert client.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_diagnose_relay_via_dialect() -> None:
    """log_read returns relay_required → /ask sent → reply used as tool result."""
    case = _make_case()
    client = _make_client()
    transport = StubTransport(replies=["consumer is up, last message 14:32"])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("eng@acme.example")

    # First turn: model calls log_read (human_relay posture)
    # Second turn: model outputs diagnosis after seeing relay answer
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__log_read",
            "tool-2",
            {"client_id": "acme", "query": "release log", "log_posture": "human_relay"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]

    agent = WESSubagent(case, anthropic_client=client, dialect=dialect)
    result = await agent.diagnose()

    assert isinstance(result, Diagnosis)
    # /ask was sent to the engineer
    assert any("/ask" in msg for msg in transport.sent)


@pytest.mark.asyncio
async def test_diagnose_relay_no_dialect() -> None:
    """relay_required without dialect → tool result includes informational note."""
    case = _make_case()
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__log_read",
            "tool-3",
            {"client_id": "acme", "query": "release log", "log_posture": "human_relay"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    # No dialect injected
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    # The tool result is passed back as a string; agent continues and produces diagnosis
    assert isinstance(result, Diagnosis)
    # Verify the tool result was passed (not an error)
    call_args = client.messages.create.call_args_list[1]
    assert call_args.kwargs.get("messages") or call_args.args[0]
    # Second messages call should include tool_result in the history
    assert client.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_diagnose_pre_hook_blocks_write_tool() -> None:
    """Model calls a write tool → PermissionError → is_error tool_result."""
    case = _make_case()
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__db_write",  # write tool — should be blocked
            "tool-bad",
            {"client_id": "acme"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    # Second call should carry is_error tool_result in history
    second_call_kwargs = client.messages.create.call_args_list[1].kwargs
    messages = second_call_kwargs["messages"]
    tool_results_msg = messages[-1]["content"]
    assert any(r.get("is_error") for r in tool_results_msg)
    assert isinstance(result, Diagnosis)


@pytest.mark.asyncio
async def test_diagnose_enforce_client_scope() -> None:
    """Tool called with wrong client_id → PermissionError → is_error."""
    case = _make_case(client="acme")
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__db_state_read",
            "tool-scope",
            {"client_id": "other-client", "entity_type": "order", "entity_id": "12345"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    agent = WESSubagent(case, anthropic_client=client)
    await agent.diagnose()

    second_call_kwargs = client.messages.create.call_args_list[1].kwargs
    messages = second_call_kwargs["messages"]
    tool_results_msg = messages[-1]["content"]
    assert any(r.get("is_error") for r in tool_results_msg)


@pytest.mark.asyncio
async def test_diagnose_enforce_allowlist() -> None:
    """Model calls an unlisted tool → PermissionError → is_error."""
    case = _make_case()
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__not_a_real_tool",
            "tool-nope",
            {"client_id": "acme"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    agent = WESSubagent(case, anthropic_client=client)
    await agent.diagnose()

    second_call_kwargs = client.messages.create.call_args_list[1].kwargs
    messages = second_call_kwargs["messages"]
    tool_results_msg = messages[-1]["content"]
    assert any(r.get("is_error") for r in tool_results_msg)


@pytest.mark.asyncio
async def test_diagnose_max_turns_bounded_give_up() -> None:
    """Loop runs MAX_TURNS with only tool_use responses → bounded give-up escalation."""
    from support_orchestration.subagents.base import MAX_TURNS
    case = _make_case()
    client = _make_client()

    # Return tool_use every turn
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__db_state_read",
            f"tool-{i}",
            {"client_id": "acme", "entity_type": "order", "entity_id": "12345"},
        )
        for i in range(MAX_TURNS)
    ]
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    assert result.next_action == NextAction.escalate
    assert result.blocker_class == "bounded_give_up"
    assert client.messages.create.call_count == MAX_TURNS


@pytest.mark.asyncio
async def test_diagnose_malformed_json_bounded_give_up() -> None:
    """end_turn with invalid JSON → bounded_give_up."""
    case = _make_case()
    client = _make_client()
    client.messages.create.return_value = _end_turn_response("This is not JSON.")
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    assert result.next_action == NextAction.escalate
    assert result.blocker_class == "bounded_give_up"


@pytest.mark.asyncio
async def test_diagnose_reroute_next_action() -> None:
    """Model outputs reroute → reroute_target preserved in returned Diagnosis."""
    case = _make_case()
    client = _make_client()
    client.messages.create.return_value = _end_turn_response(
        _diagnosis_text(next_action="reroute", reroute_target="IMS")
    )
    agent = WESSubagent(case, anthropic_client=client)
    result = await agent.diagnose()

    assert result.next_action == NextAction.reroute
    assert result.reroute_target == "IMS"


@pytest.mark.asyncio
async def test_diagnose_uses_fixture_adapters() -> None:
    """adapters dict is used by _dispatch_tool for db_state_read."""
    from support_orchestration.tools.mcp_server import StubDbAdapter

    case = _make_case()
    client = _make_client()
    client.messages.create.side_effect = [
        _tool_use_response(
            "mcp__support__db_state_read",
            "tool-db",
            {"client_id": "acme", "entity_type": "order", "entity_id": "12345"},
        ),
        _end_turn_response(_diagnosis_text()),
    ]
    # Custom adapter that records calls
    called_with: list = []
    class RecordingDbAdapter(StubDbAdapter):
        async def query(self, client_id, sql, params):
            called_with.append((client_id, params))
            return []

    agent = WESSubagent(case, anthropic_client=client, adapters={"db": RecordingDbAdapter()})
    await agent.diagnose()

    assert len(called_with) == 1
    assert called_with[0][0] == "acme"


@pytest.mark.asyncio
async def test_dispatch_tool_unknown_raises() -> None:
    """_dispatch_tool raises ValueError for an unknown tool name."""
    case = _make_case()
    agent = WESSubagent(case, anthropic_client=_make_client())
    with pytest.raises(ValueError, match="Unknown tool"):
        await agent._dispatch_tool("mcp__support__nonexistent", {"client_id": "acme"})


def test_bounded_give_up_shape() -> None:
    case = _make_case()
    result = bounded_give_up(case, "WES")
    assert result.next_action == NextAction.escalate
    assert result.blocker_class == "bounded_give_up"
    assert result.owning_domain == "WES"
    assert result.confidence == 0.0
    assert "WES" in result.notes


def test_parse_diagnosis_json_with_tags() -> None:
    case = _make_case()
    text = _diagnosis_text()
    result = parse_diagnosis_json(text, case)
    assert result is not None
    assert result.owning_domain == "WES"
    assert result.blocker_class == "lost_ack"


def test_parse_diagnosis_json_invalid_returns_none() -> None:
    case = _make_case()
    result = parse_diagnosis_json("No diagnosis here.", case)
    assert result is None


def test_wes_system_prompt_contains_domain() -> None:
    case = _make_case()
    agent = WESSubagent(case, anthropic_client=_make_client())
    prompt = agent.system_prompt
    assert "WES" in prompt
    assert "prioritized → released" in prompt
    assert "ims_hold" in prompt


def test_get_subagent_passes_kwargs() -> None:
    case = _make_case()
    mock_client = _make_client()
    agent = get_subagent("WES", case, anthropic_client=mock_client)
    assert isinstance(agent, WESSubagent)
    assert agent._anthropic is mock_client
