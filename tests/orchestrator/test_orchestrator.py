"""Tests for the Prompt-6 orchestrator — triage, routing, human dialogue, run loop.

All tests are offline (no Anthropic API key required). LLM steps are exercised
via the deterministic fallback path (anthropic_client=None).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case, CaseStatus, Diagnosis, Priority
from support_orchestration.models.diagnosis import NextAction, ProposedFix
from support_orchestration.orchestrator.orchestrator import Orchestrator, _is_rejection
from support_orchestration.orchestrator.triage import (
    TriageDecision,
    _deterministic_triage,
    _parse_triage_json,
    run_triage,
)
from support_orchestration.storage.state_store import CaseStore
from support_orchestration.subagents.base import BaseSubagent

# ── Test helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_case(**overrides: object) -> Case:
    now = _now()
    defaults: dict[str, object] = {
        "jira_ticket_id": "WH-100",
        "client": "acme",
        "priority": Priority.P2,
        "created_at": now,
        "sla_deadline": now + timedelta(hours=8),
        "entity_type": "order",
        "entity_id": "ORD-42",
        "entity_current_state": "prioritized",
        "owning_domain": "WES",
        "stuck_transition": "prioritized → released",
        "assignee_email": "eng@acme.com",
    }
    defaults.update(overrides)
    return Case(**defaults)  # type: ignore[arg-type]


def _make_diagnosis(**overrides: object) -> Diagnosis:
    defaults: dict[str, object] = {
        "entity": {"type": "order", "id": "ORD-42", "current_state": "prioritized"},
        "stuck_transition": "prioritized → released",
        "owning_domain": "WES",
        "root_cause": "Release ack lost — WES emitted but picking engine did not ack",
        "blocker_class": "missing_ack",
        "confidence": 0.85,
        "next_action": NextAction.propose_to_human,
        "proposed_fix": ProposedFix(
            summary="Re-drive the WES release for ORD-42",
            human_steps=["Verify consumer is alive", "Re-emit release", "Confirm state transition"],
            reversible=True,
            verification="Order ORD-42 transitions to 'released' within 2 min",
        ),
    }
    defaults.update(overrides)
    return Diagnosis(**defaults)  # type: ignore[arg-type]


class _SubagentStub(BaseSubagent):
    """Controllable subagent stub that returns a pre-set Diagnosis."""

    def __init__(self, case: Case, diagnosis: Diagnosis) -> None:
        super().__init__(case)
        self._diagnosis = diagnosis

    @property
    def system_prompt(self) -> str:
        return "stub"

    async def diagnose(self) -> Diagnosis:
        return self._diagnosis


def _stub_factory(diagnosis: Diagnosis) -> object:
    """Return a subagent factory that always yields a _SubagentStub."""
    def factory(domain: str, case: Case) -> BaseSubagent:
        return _SubagentStub(case, diagnosis)
    return factory


def _make_orchestrator(
    case: Case,
    *,
    replies: list[str] | None = None,
    diagnosis: Diagnosis | None = None,
    store: CaseStore | None = None,
) -> tuple[Orchestrator, StubTransport]:
    transport = StubTransport(replies=replies or [])
    dialect = DialectManager(transport, case)
    dialect.set_conversation_ref("ref")
    factory = _stub_factory(diagnosis) if diagnosis else None
    orch = Orchestrator(
        case,
        dialect=dialect,
        subagent_factory=factory,  # type: ignore[arg-type]
        state_store=store,
    )
    return orch, transport


# ── Deterministic triage ──────────────────────────────────────────────────────

class TestDeterministicTriage:
    def test_routes_when_domain_already_set(self) -> None:
        case = _make_case()
        result = _deterministic_triage(case)
        assert result.owning_domain == "WES"
        assert result.next_action == TriageDecision.route
        assert result.confidence >= 0.70

    def test_routes_from_map_lookup_when_domain_unset(self) -> None:
        case = _make_case(owning_domain=None, stuck_transition=None)
        result = _deterministic_triage(case)
        assert result.owning_domain == "WES"
        assert result.next_action == TriageDecision.route

    def test_escalates_when_entity_type_unknown(self) -> None:
        case = _make_case(
            entity_type=None, entity_current_state=None,
            owning_domain=None, stuck_transition=None,
        )
        result = _deterministic_triage(case)
        assert result.next_action == TriageDecision.escalate
        assert result.owning_domain is None
        assert result.confidence < 0.40

    def test_escalates_when_state_not_in_map(self) -> None:
        case = _make_case(
            entity_current_state="completely_unknown_state",
            owning_domain=None, stuck_transition=None,
        )
        result = _deterministic_triage(case)
        assert result.next_action == TriageDecision.escalate


# ── run_triage (async entry point) ────────────────────────────────────────────

class TestRunTriage:
    async def test_no_client_uses_deterministic(self) -> None:
        case = _make_case()
        result = await run_triage(case, None)
        assert result.owning_domain == "WES"
        assert result.next_action == TriageDecision.route

    async def test_sla_tight_forces_escalate(self) -> None:
        case = _make_case(sla_deadline=_now() + timedelta(minutes=5))
        result = await run_triage(case, None)
        assert result.next_action == TriageDecision.escalate
        assert "SLA" in result.reasoning or "SLA" in (result.hypothesis or "")

    async def test_sla_tight_even_with_known_domain(self) -> None:
        """High-confidence routing should still escalate when SLA is almost gone."""
        case = _make_case(
            owning_domain="WES",
            stuck_transition="prioritized → released",
            sla_deadline=_now() + timedelta(minutes=3),
        )
        result = await run_triage(case, None)
        assert result.next_action == TriageDecision.escalate


# ── JSON parsing ──────────────────────────────────────────────────────────────

class TestParseTriageJson:
    def test_valid_route_json(self) -> None:
        text = (
            '{"owning_domain": "WES", "confidence": 0.9, '
            '"stuck_transition": "prioritized → released", '
            '"hypothesis": "ack lost", "alternative_hypotheses": [], "next_action": "route"}'
        )
        result = _parse_triage_json(text)
        assert result.owning_domain == "WES"
        assert result.next_action == TriageDecision.route
        assert result.confidence == 0.9

    def test_json_embedded_in_prose(self) -> None:
        text = (
            'Here is my analysis: '
            '{"owning_domain": "IMS", "confidence": 0.5, "next_action": "probe"} done.'
        )
        result = _parse_triage_json(text)
        assert result.owning_domain == "IMS"
        assert result.next_action == TriageDecision.probe

    def test_invalid_json_returns_escalate(self) -> None:
        result = _parse_triage_json("Sorry, I cannot determine the domain.")
        assert result.next_action == TriageDecision.escalate
        assert result.owning_domain is None

    def test_unknown_next_action_becomes_escalate(self) -> None:
        text = '{"owning_domain": "WES", "confidence": 0.8, "next_action": "unknown_action"}'
        result = _parse_triage_json(text)
        assert result.next_action == TriageDecision.escalate

    def test_missing_confidence_defaults_to_half(self) -> None:
        text = '{"owning_domain": "WES", "next_action": "route"}'
        result = _parse_triage_json(text)
        assert result.confidence == 0.5


# ── is_rejection ─────────────────────────────────────────────────────────────

class TestIsRejection:
    def test_no_is_rejection(self) -> None:
        assert _is_rejection("no") is True
        assert _is_rejection("No that's not right") is True
        assert _is_rejection("nope") is True
        assert _is_rejection("disagree") is True
        assert _is_rejection("wrong, it's an IMS hold") is True

    def test_agree_not_rejection(self) -> None:
        assert _is_rejection("agree") is False
        assert _is_rejection("yes that's correct") is False
        assert _is_rejection("approved") is False
        assert _is_rejection("ok looks right") is False

    def test_edge_cases(self) -> None:
        assert _is_rejection("normally yes but not here") is False  # doesn't start with rejection
        assert _is_rejection("incorrect") is True


# ── Orchestrator._triage ──────────────────────────────────────────────────────

class TestOrchestratorTriage:
    async def test_sets_owning_domain_on_case(self) -> None:
        case = _make_case()
        orch, _ = _make_orchestrator(case)
        await orch._triage()
        assert case.owning_domain == "WES"

    async def test_triage_adds_trail_entry(self) -> None:
        case = _make_case()
        orch, _ = _make_orchestrator(case)
        await orch._triage()
        actors = [t.actor for t in case.trail]
        assert "orchestrator" in actors
        actions = [t.action for t in case.trail]
        assert "triage" in actions

    async def test_escalates_on_unknown_entity(self) -> None:
        case = _make_case(
            entity_type=None, entity_current_state=None,
            owning_domain=None, stuck_transition=None,
        )
        orch, transport = _make_orchestrator(case)
        await orch._triage()
        assert case.status == CaseStatus.escalated
        # A /status info message should have been sent
        assert any("/status" in msg for msg in transport.sent)

    async def test_escalates_on_sla_tight(self) -> None:
        case = _make_case(sla_deadline=_now() + timedelta(minutes=4))
        orch, _ = _make_orchestrator(case)
        await orch._triage()
        assert case.status == CaseStatus.escalated


# ── Orchestrator._route_and_diagnose ─────────────────────────────────────────

class TestOrchestratorRouteAndDiagnose:
    async def test_happy_path_propose_and_approve(self) -> None:
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, transport = _make_orchestrator(
            case, replies=["agree", "approved"], diagnosis=diagnosis,
        )
        await orch._route_and_diagnose()
        assert case.fix_approved is True
        assert case.status == CaseStatus.fix_proposed
        # Validate + approve messages sent
        sent = " ".join(transport.sent)
        assert "/validate" in sent
        assert "/approve" in sent

    async def test_reroute_to_different_domain(self) -> None:
        """WES bounces back; IMS subagent then proposes a fix."""
        case = _make_case()
        wes_diagnosis = _make_diagnosis(
            owning_domain="WES",
            next_action=NextAction.reroute,
            reroute_target="IMS",
            proposed_fix=None,
            root_cause="IMS hold suspected",
            blocker_class="ims_hold",
        )
        ims_diagnosis = _make_diagnosis(
            owning_domain="IMS",
            stuck_transition="prioritized → released",
            root_cause="Count hold on item — needs release",
            blocker_class="ims_count_hold",
        )

        call_count = 0

        def factory(domain: str, c: Case) -> BaseSubagent:
            nonlocal call_count
            call_count += 1
            if domain == "WES":
                return _SubagentStub(c, wes_diagnosis)
            return _SubagentStub(c, ims_diagnosis)

        transport = StubTransport(replies=["agree", "approved"])
        dialect = DialectManager(transport, case)
        dialect.set_conversation_ref("ref")
        orch = Orchestrator(case, dialect=dialect, subagent_factory=factory)

        await orch._route_and_diagnose()

        assert call_count == 2
        assert case.fix_approved is True
        assert "WES" in case.reroute_guard
        assert "IMS" in case.reroute_guard

    async def test_reroute_guard_blocks_revisit(self) -> None:
        """If a domain is suggested again after being visited, orchestrator escalates."""
        case = _make_case()
        # WES bounces to IMS; IMS bounces back to WES (already visited)
        wes_diagnosis = _make_diagnosis(
            next_action=NextAction.reroute, reroute_target="IMS", proposed_fix=None,
        )
        ims_diagnosis = _make_diagnosis(
            owning_domain="IMS",
            next_action=NextAction.reroute, reroute_target="WES",  # loop back!
            proposed_fix=None,
        )

        def factory(domain: str, c: Case) -> BaseSubagent:
            if domain == "WES":
                return _SubagentStub(c, wes_diagnosis)
            return _SubagentStub(c, ims_diagnosis)

        orch, transport = _make_orchestrator(case)
        orch._subagent_factory = factory  # type: ignore[assignment]
        await orch._route_and_diagnose()

        assert case.status == CaseStatus.escalated
        assert any("reroute guard" in msg.lower() for msg in transport.sent)

    async def test_subagent_escalate_escalates_orchestrator(self) -> None:
        case = _make_case()
        diagnosis = _make_diagnosis(
            next_action=NextAction.escalate, proposed_fix=None, notes="Novel failure mode",
        )
        orch, transport = _make_orchestrator(case, diagnosis=diagnosis)
        await orch._route_and_diagnose()
        assert case.status == CaseStatus.escalated

    async def test_need_info_escalates_in_prompt_6(self) -> None:
        """In P6, human relay need_info falls through to escalate."""
        case = _make_case()
        diagnosis = _make_diagnosis(
            next_action=NextAction.need_info,
            needs_from_human="What is the current queue depth on channel X?",
            proposed_fix=None,
        )
        orch, _ = _make_orchestrator(case, diagnosis=diagnosis)
        await orch._route_and_diagnose()
        assert case.status == CaseStatus.escalated

    async def test_human_rejection_escalates(self) -> None:
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, transport = _make_orchestrator(
            case, replies=["no that's wrong"], diagnosis=diagnosis,
        )
        await orch._route_and_diagnose()
        assert case.status == CaseStatus.escalated
        assert "rejected" in " ".join(e.action for e in case.trail)

    async def test_max_reroutes_exhausted(self) -> None:
        """After MAX_REROUTES bounces with no resolution, orchestrator escalates."""
        case = _make_case()
        domains = ["WES", "GTP_PICKING", "GTP_DECANT", "IMS"]

        def factory(domain: str, c: Case) -> BaseSubagent:
            idx = domains.index(domain) if domain in domains else 0
            next_domain = domains[idx + 1] if idx + 1 < len(domains) else None
            diag = _make_diagnosis(
                owning_domain=domain,
                next_action=NextAction.reroute,
                reroute_target=next_domain,
                proposed_fix=None,
            )
            return _SubagentStub(c, diag)

        orch, transport = _make_orchestrator(case)
        orch._subagent_factory = factory  # type: ignore[assignment]
        await orch._route_and_diagnose()

        assert case.status == CaseStatus.escalated

    async def test_reroute_without_target_escalates(self) -> None:
        case = _make_case()
        diagnosis = _make_diagnosis(
            next_action=NextAction.reroute, reroute_target=None, proposed_fix=None,
        )
        orch, _ = _make_orchestrator(case, diagnosis=diagnosis)
        await orch._route_and_diagnose()
        assert case.status == CaseStatus.escalated


# ── Orchestrator.run (full flow) ──────────────────────────────────────────────

class TestOrchestratorRun:
    async def test_full_happy_path_resolves(self, tmp_path: Path) -> None:
        store = CaseStore(db_path=tmp_path / "state.db")
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, transport = _make_orchestrator(
            case, replies=["agree", "approved"], diagnosis=diagnosis, store=store,
        )
        await orch.run()

        assert case.status == CaseStatus.resolved
        assert case.fix_approved is True
        assert case.resolution_summary is not None

        # Case persisted
        loaded = store.load_case(case.case_id)
        assert loaded is not None
        assert loaded.status == CaseStatus.resolved

    async def test_full_escalation_path(self, tmp_path: Path) -> None:
        store = CaseStore(db_path=tmp_path / "state.db")
        case = _make_case(
            entity_type=None, entity_current_state=None,
            owning_domain=None, stuck_transition=None,
        )
        orch, transport = _make_orchestrator(case, store=store)
        await orch.run()

        assert case.status == CaseStatus.escalated
        loaded = store.load_case(case.case_id)
        assert loaded is not None
        assert loaded.status == CaseStatus.escalated

    async def test_run_persists_checkpoints(self, tmp_path: Path) -> None:
        """State store should have at least one record by the time run() completes."""
        store = CaseStore(db_path=tmp_path / "state.db")
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, _ = _make_orchestrator(
            case, replies=["agree", "approved"], diagnosis=diagnosis, store=store,
        )
        await orch.run()
        assert store.load_case(case.case_id) is not None

    async def test_warm_start_dossier_sent(self) -> None:
        """A /status info message should be sent before the validation loop."""
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, transport = _make_orchestrator(
            case, replies=["agree", "approved"], diagnosis=diagnosis,
        )
        await orch.run()
        assert any("/status" in msg for msg in transport.sent)

    async def test_trail_records_full_flow(self) -> None:
        case = _make_case()
        diagnosis = _make_diagnosis()
        orch, _ = _make_orchestrator(
            case, replies=["agree", "approved"], diagnosis=diagnosis,
        )
        await orch.run()

        actions = {e.action for e in case.trail}
        assert "dossier_sent" in actions
        assert "triage" in actions
        assert "routed_to" in actions
        assert "fix_approved" in actions

    async def test_default_dialect_uses_stub_transport(self) -> None:
        """Orchestrator created with no dialect should not raise."""
        case = _make_case()
        diagnosis = _make_diagnosis(
            next_action=NextAction.escalate, proposed_fix=None, notes="test",
        )
        # No dialect argument — orchestrator builds StubTransport internally
        orch = Orchestrator(case, subagent_factory=_stub_factory(diagnosis))  # type: ignore[arg-type]
        await orch.run()
        assert case.status == CaseStatus.escalated


# ── render helpers ────────────────────────────────────────────────────────────

class TestPromptRenderers:
    def test_render_case_for_triage_includes_key_fields(self) -> None:
        from support_orchestration.orchestrator.prompts import render_case_for_triage
        case = _make_case()
        text = render_case_for_triage(case)
        assert "WH-100" in text
        assert "acme" in text
        assert "prioritized" in text
        assert "ORD-42" in text

    def test_render_warm_start_dossier_includes_entity(self) -> None:
        from support_orchestration.orchestrator.prompts import render_warm_start_dossier
        case = _make_case()
        text = render_warm_start_dossier(case)
        assert "WH-100" in text
        assert "WES" in text

    def test_render_lifecycle_map_text(self) -> None:
        from support_orchestration.orchestrator.prompts import render_lifecycle_map_text
        from support_orchestration.orchestrator.triage import load_lifecycle_map
        map_data = load_lifecycle_map("order")
        text = render_lifecycle_map_text(map_data)
        assert "ORDER" in text
        assert "WES" in text
        assert "prioritized" in text
