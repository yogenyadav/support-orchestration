"""Prompt 7 orchestrator tests — C6 reply parsing + default subagent factory wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case, Priority
from support_orchestration.models.case import CaseStatus
from support_orchestration.models.diagnosis import Diagnosis, NextAction, ProposedFix
from support_orchestration.orchestrator.orchestrator import Orchestrator
from support_orchestration.subagents.base import BaseSubagent


def _make_case(client: str = "acme") -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        jira_ticket_id="WH-P7-001",
        client=client,
        priority=Priority.P2,
        created_at=now,
        sla_deadline=now + timedelta(hours=8),
        entity_type="order",
        entity_id="12345",
        entity_current_state="prioritized",
        stuck_transition="prioritized → released",
        owning_domain="WES",
        confidence=0.9,
        assignee_email="eng@acme.example",
    )


def _make_haiku_client(response_text: str) -> MagicMock:
    block = MagicMock()
    block.text = response_text
    resp = MagicMock()
    resp.content = [block]
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    return client


def _make_diagnosis(next_action: NextAction = NextAction.propose_to_human, **kwargs) -> Diagnosis:
    return Diagnosis(
        entity={"type": "order", "id": "12345", "current_state": "prioritized"},
        stuck_transition="prioritized → released",
        owning_domain="WES",
        root_cause="Lost ack from picking engine.",
        blocker_class="lost_ack",
        confidence=0.9,
        next_action=next_action,
        proposed_fix=ProposedFix(
            summary="Restart WES ack listener.",
            human_steps=["Restart ack listener"],
            reversible=True,
            verification="Order advances to released within 90s.",
        ),
        **kwargs,
    )


class _StubSubagent(BaseSubagent):
    def __init__(self, case, diagnosis):
        super().__init__(case)
        self._preset = diagnosis

    @property
    def system_prompt(self) -> str:
        return "stub"

    async def diagnose(self) -> Diagnosis:
        return self._preset


@pytest.mark.asyncio
async def test_validate_approve_uses_c6_affirm() -> None:
    """C6 returns 'affirm' for engineer reply → validation passes, approve sent."""
    case = _make_case()
    haiku_client = _make_haiku_client("affirm")
    transport = StubTransport(replies=["yes agreed", "approved"])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("eng@acme.example")
    diag = _make_diagnosis()
    orch = Orchestrator(
        case,
        dialect=dialect,
        anthropic_client=haiku_client,
        subagent_factory=lambda d, c: _StubSubagent(c, diag),
    )
    await orch._validate_and_approve(diag)

    assert case.fix_approved is True
    assert any("/validate" in m for m in transport.sent)
    assert any("/approve" in m for m in transport.sent)


@pytest.mark.asyncio
async def test_validate_approve_c6_reject_escalates() -> None:
    """C6 returns 'reject' → orchestrator escalates, no /approve sent."""
    case = _make_case()
    haiku_client = _make_haiku_client("reject")
    transport = StubTransport(replies=["no that is wrong"])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("eng@acme.example")
    diag = _make_diagnosis()
    orch = Orchestrator(
        case,
        dialect=dialect,
        anthropic_client=haiku_client,
        subagent_factory=lambda d, c: _StubSubagent(c, diag),
    )
    await orch._validate_and_approve(diag)

    assert case.status == CaseStatus.escalated
    assert not any("/approve" in m for m in transport.sent)


@pytest.mark.asyncio
async def test_validate_approve_no_client_uses_regex_fallback() -> None:
    """No anthropic_client → regex _is_rejection used → 'no' → escalate."""
    case = _make_case()
    transport = StubTransport(replies=["no wrong diagnosis"])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("eng@acme.example")
    diag = _make_diagnosis()
    # anthropic_client=None → regex fallback
    orch = Orchestrator(
        case,
        dialect=dialect,
        anthropic_client=None,
        subagent_factory=lambda d, c: _StubSubagent(c, diag),
    )
    await orch._validate_and_approve(diag)
    assert case.status == CaseStatus.escalated


@pytest.mark.asyncio
async def test_default_subagent_factory_wires_anthropic_client() -> None:
    """Default factory passes anthropic_client to the subagent."""
    case = _make_case()
    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock()

    orch = Orchestrator(case, anthropic_client=mock_client)
    subagent = orch._subagent_factory("WES", case)
    assert subagent._anthropic is mock_client


@pytest.mark.asyncio
async def test_default_subagent_factory_wires_dialect() -> None:
    """Default factory passes dialect to the subagent."""
    case = _make_case()
    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock()

    orch = Orchestrator(case, anthropic_client=mock_client)
    subagent = orch._subagent_factory("WES", case)
    assert subagent._dialect is orch._dialect


@pytest.mark.asyncio
async def test_need_info_escalates_with_question() -> None:
    """Subagent returning need_info → orchestrator escalates with the question."""
    case = _make_case()
    diag = _make_diagnosis(
        next_action=NextAction.need_info,
        needs_from_human="What is the current state of the picking engine consumer?",
    )
    transport = StubTransport()
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("eng@acme.example")
    orch = Orchestrator(
        case,
        dialect=dialect,
        anthropic_client=None,
        subagent_factory=lambda d, c: _StubSubagent(c, diag),
    )
    await orch._route_and_diagnose()

    assert case.status == CaseStatus.escalated
    sent_text = " ".join(transport.sent)
    assert "picking engine" in sent_text.lower() or "relay" in sent_text.lower()
