"""Tests for Prompt-8 orchestrator additions — jira_client wiring + write_resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from support_orchestration.glue.jira import StubJiraClient
from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case, CaseStatus, Diagnosis, Priority
from support_orchestration.models.diagnosis import NextAction, ProposedFix
from support_orchestration.orchestrator.orchestrator import Orchestrator


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_case(**overrides: object) -> Case:
    now = _now()
    defaults: dict[str, object] = {
        "jira_ticket_id": "WH-200",
        "client": "acme",
        "priority": Priority.P2,
        "created_at": now,
        "sla_deadline": now + timedelta(hours=8),
        "entity_type": "order",
        "entity_id": "ORD-99",
        "entity_current_state": "prioritized",
        "owning_domain": "WES",
        "stuck_transition": "prioritized → released",
        "assignee_email": "eng@acme.com",
    }
    defaults.update(overrides)
    return Case(**defaults)  # type: ignore[arg-type]


def _make_diagnosis(**overrides: object) -> Diagnosis:
    defaults: dict[str, object] = {
        "entity": {"type": "order", "id": "ORD-99", "current_state": "prioritized"},
        "stuck_transition": "prioritized → released",
        "owning_domain": "WES",
        "root_cause": "Consumer ack lost",
        "blocker_class": "missing_ack",
        "confidence": 0.9,
        "proposed_fix": ProposedFix(
            summary="Re-drive release",
            human_steps=["Restart consumer", "Re-emit release"],
            sql_statement="UPDATE orders SET state='released' WHERE id='ORD-99'",
            reversible=True,
            verification="Order reaches released within 2 min",
        ),
        "next_action": NextAction.propose_to_human,
    }
    defaults.update(overrides)
    return Diagnosis(**defaults)  # type: ignore[arg-type]


def _make_orchestrator(
    case: Case | None = None,
    jira_client: StubJiraClient | None = None,
    replies: list[str] | None = None,
) -> tuple[Orchestrator, StubTransport]:
    case = case or _make_case()
    transport = StubTransport(replies=replies or ["ok", "approved"])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref(case.assignee_email or "ref")

    from support_orchestration.subagents.base import BaseSubagent

    class _SubagentStub(BaseSubagent):
        async def diagnose(self) -> Diagnosis:
            return _make_diagnosis()

    def _factory(domain: str, c: Case) -> BaseSubagent:
        return _SubagentStub(c)  # BaseSubagent takes (case, ...) not (domain, case)

    orch = Orchestrator(
        case,
        dialect=dialect,
        jira_client=jira_client,
        subagent_factory=_factory,
    )
    return orch, transport


# ── write_resolution calls Jira ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_resolution_writes_to_jira():
    """Full run: after /approve, write_resolution should call jira_client.write_resolution."""
    case = _make_case()
    jira = StubJiraClient()
    orch, transport = _make_orchestrator(case, jira_client=jira, replies=["yes", "approved"])

    await orch.run()

    assert len(jira.written_resolutions) == 1
    rec = jira.written_resolutions[0]
    assert rec["ticket_id"] == "WH-200"
    assert "Consumer ack lost" in rec["diagnosis_summary"]


@pytest.mark.asyncio
async def test_write_resolution_without_jira_client_does_not_raise():
    """If no jira_client is injected, _write_resolution silently skips the Jira write."""
    case = _make_case()
    orch, _ = _make_orchestrator(case, jira_client=None, replies=["yes", "approved"])
    # Should complete without error
    await orch.run()
    assert case.status == CaseStatus.resolved


@pytest.mark.asyncio
async def test_write_resolution_includes_proposed_fix():
    """Resolution record includes fix SQL in the fix_summary field."""
    case = _make_case()
    jira = StubJiraClient()
    orch, _ = _make_orchestrator(case, jira_client=jira, replies=["yes", "approved"])

    await orch.run()

    rec = jira.written_resolutions[0]
    assert "UPDATE orders" in rec["fix_summary"]


@pytest.mark.asyncio
async def test_escalated_case_does_not_write_to_jira():
    """Escalated cases must not write a resolution to Jira."""
    case = _make_case()
    jira = StubJiraClient()
    transport = StubTransport(replies=[])

    class _EscalatingSubagent:
        async def diagnose(self) -> Diagnosis:
            return _make_diagnosis(next_action=NextAction.escalate, notes="can't diagnose")

    from support_orchestration.subagents.base import BaseSubagent

    class _Stub(BaseSubagent):
        async def diagnose(self) -> Diagnosis:
            return _make_diagnosis(next_action=NextAction.escalate, notes="can't diagnose")

    def _factory(domain: str, c: Case):
        return _Stub(c)

    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("ref")
    orch = Orchestrator(case, dialect=dialect, jira_client=jira, subagent_factory=_factory)
    await orch.run()

    assert case.status == CaseStatus.escalated
    assert len(jira.written_resolutions) == 0


@pytest.mark.asyncio
async def test_orchestrator_accepts_jira_client_kwarg():
    """Orchestrator.__init__ accepts jira_client keyword argument."""
    case = _make_case()
    jira = StubJiraClient()
    orch = Orchestrator(case, jira_client=jira)
    assert orch._jira_client is jira


# ── _formulate_memory tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_formulate_memory_without_anthropic_returns_raw():
    """Without an API client, _formulate_memory returns the raw diagnosis_summary."""
    case = _make_case()
    orch, _ = _make_orchestrator(case)
    result = await orch._formulate_memory("WES queue full", "Restart consumer")
    assert result == "WES queue full"


@pytest.mark.asyncio
async def test_formulate_memory_with_anthropic_calls_haiku():
    """With API client, _formulate_memory calls claude-haiku-4-5 and returns its output."""
    from unittest.mock import AsyncMock, MagicMock

    formatted = (
        "**Context**: Order stuck at prioritized in WES.\n"
        "**Root Cause**: Consumer ack dropped.\n"
        "**Resolution**: Restarted consumer and re-emitted release.\n"
        "**Watch Out For**: Repeat during high-throughput periods."
    )
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=formatted)]

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create = AsyncMock(return_value=mock_resp)

    case = _make_case()
    orch = Orchestrator(case, anthropic_client=mock_anthropic)

    result = await orch._formulate_memory("Consumer ack lost", "Restart WES")

    assert result == formatted
    call_kwargs = mock_anthropic.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["max_tokens"] == 200


@pytest.mark.asyncio
async def test_write_resolution_passes_formulated_summary_to_vector():
    """_write_resolution calls _formulate_memory and writes its result as summary."""
    from unittest.mock import AsyncMock, patch

    from support_orchestration.tools.mcp_server import StubVectorAdapter

    case = _make_case()
    vec = StubVectorAdapter()
    orch, _ = _make_orchestrator(case, replies=["ok", "approved"])
    orch._vector = vec

    expected_summary = "**Context**: Order stuck.\n**Root Cause**: Queue full."
    with patch.object(
        orch, "_formulate_memory", AsyncMock(return_value=expected_summary)
    ) as mock_fm:
        await orch.run()

    mock_fm.assert_called_once()
    assert len(vec.written) == 1
    assert vec.written[0]["summary"] == expected_summary
