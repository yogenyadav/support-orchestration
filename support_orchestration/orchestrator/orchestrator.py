"""Per-incident orchestrator (docs/3-agent-design.md §3.1, §3.4).

One instance per active incident (≤10 concurrent). Responsibilities:
  - ingest Case object
  - assess priority / SLA
  - locate stuck entity on lifecycle map
  - triage to owning domain (C1 Haiku classify → C3 Sonnet route)
  - apply escalate-vs-probe rule
  - route to domain subagent; handle bounce-back and reroute loop
  - manage human dialogue via Teams dialect (/validate → /approve)
  - persist Case checkpoints to state store
  - write final Jira resolution record (stub — full Jira write in Prompt 8)

docs/4 §4.2 model routing:
  C1  classify   → Haiku 4.5
  C3  triage     → Sonnet 4.6 (prompt-cached system + map)
  C8  dossier    → Sonnet 4.6
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from support_orchestration.config.base import MODEL_HAIKU, MODEL_SONNET
from support_orchestration.glue.jira import JiraClient
from support_orchestration.glue.teams import DialectManager, StubTransport, c6_interpret_reply
from support_orchestration.models import Case, CaseStatus, Diagnosis
from support_orchestration.models.diagnosis import NextAction
from support_orchestration.orchestrator.prompts import render_case_for_triage, render_warm_start_dossier
from support_orchestration.orchestrator.triage import TriageDecision, run_triage
from support_orchestration.subagents.base import BaseSubagent, get_subagent
from support_orchestration.tools.mcp_server import VectorStoreAdapter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Model routing per docs/4 §4.2
MODEL_TRIAGE = MODEL_SONNET   # C3, C8
MODEL_MEMORY = MODEL_HAIKU    # memory formulation before pgvector write

MAX_REROUTES = 3              # hard cap on domain re-routing before forced escalation

_MEMORY_SYSTEM = (
    "You write concise knowledge-base memory records for warehouse support incidents. "
    "Output exactly four labelled lines, nothing else:\n"
    "**Context**: <one sentence — what was stuck and in which system>\n"
    "**Root Cause**: <one sentence — why it got stuck>\n"
    "**Resolution**: <one sentence — what fixed it>\n"
    "**Watch Out For**: <one warning for future similar incidents>"
)


class Orchestrator:
    """Manages one incident from assignment through resolution."""

    def __init__(
        self,
        case: Case,
        *,
        dialect: DialectManager | None = None,
        anthropic_client: Any | None = None,   # anthropic.AsyncAnthropic
        state_store: Any | None = None,         # CaseStore
        jira_client: JiraClient | None = None,  # injected for write_resolution
        vector_adapter: VectorStoreAdapter | None = None,  # write-back on resolution
        subagent_factory: Callable[[str, Case], BaseSubagent] | None = None,
    ) -> None:
        self.case = case
        self._anthropic = anthropic_client
        self._store = state_store
        self._jira_client = jira_client
        self._vector = vector_adapter

        # Build a dialect with stub transport if none injected (test/offline mode)
        if dialect is None:
            stub = StubTransport()
            dialect = DialectManager(stub, case)
            # Use assignee_email as conversation ref when available
            ref = case.assignee_email or case.jira_ticket_id
            dialect.set_conversation_ref(ref)
        self._dialect = dialect

        # Default subagent factory wires anthropic_client + dialect through to diagnose()
        if subagent_factory is None:
            _client = anthropic_client
            _dial = self._dialect
            def _default_factory(domain: str, case: Case) -> BaseSubagent:
                return get_subagent(domain, case, anthropic_client=_client, dialect=_dial)
            self._subagent_factory = _default_factory
        else:
            self._subagent_factory = subagent_factory

    # ── Main run loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main orchestrator loop — called after 'assigned to' is populated."""
        logger.info(
            "Orchestrator started for %s (client=%s priority=%s)",
            self.case.jira_ticket_id, self.case.client, self.case.priority,
        )
        self.case.status = CaseStatus.triaging
        self._persist()

        try:
            await self._send_warm_start_dossier()   # C8
            await self._triage()                     # C1 → C3
            if self.case.status == CaseStatus.escalated:
                return
            await self._route_and_diagnose()         # C4 + human loop
            await self._write_resolution()
        except Exception:
            logger.exception("Orchestrator error for %s", self.case.jira_ticket_id)
            self.case.status = CaseStatus.escalated
            self._persist()
            raise

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def _send_warm_start_dossier(self) -> None:
        """C8 — build warm-start dossier and open Teams DM with the engineer."""
        if self._anthropic is not None:
            dossier = await self._build_c8_dossier()
        else:
            dossier = render_warm_start_dossier(self.case)

        await self._dialect.send_info(dossier)
        self.case.append_trail("orchestrator", "dossier_sent")
        self._persist()
        logger.info("Warm-start dossier sent for %s", self.case.jira_ticket_id)

    async def _triage(self) -> None:
        """C1 classify (if needed) + C3 triage/route. Updates case and persists."""
        result = await run_triage(self.case, self._anthropic)

        self.case.owning_domain = result.owning_domain
        self.case.confidence = result.confidence
        if result.stuck_transition and not self.case.stuck_transition:
            self.case.stuck_transition = result.stuck_transition
        if result.hypothesis and not self.case.hypothesis:
            self.case.hypothesis = result.hypothesis
        if result.alternative_hypotheses:
            self.case.alternative_hypotheses = result.alternative_hypotheses

        self.case.append_trail(
            "orchestrator",
            "triage",
            notes=(
                f"domain={result.owning_domain} "
                f"confidence={result.confidence:.2f} "
                f"action={result.next_action}"
            ),
        )
        self._persist()

        if result.next_action == TriageDecision.escalate:
            await self._escalate(
                reason=result.reasoning or "Low confidence — insufficient information",
                hypotheses=result.alternative_hypotheses,
            )

    async def _route_and_diagnose(self) -> None:
        """Escalate-vs-probe rule, subagent routing, reroute loop (max 3 bounces)."""
        for attempt in range(MAX_REROUTES + 1):
            domain = self.case.owning_domain
            if domain is None:
                await self._escalate("No owning domain identified after triage")
                return

            # Reroute guard: never revisit a domain
            if domain in self.case.reroute_guard:
                await self._escalate(
                    f"Reroute guard: domain '{domain}' already visited",
                    hypotheses=list(self.case.alternative_hypotheses),
                )
                return

            subagent = self._subagent_factory(domain, self.case)
            self.case.append_trail("orchestrator", "routed_to", notes=f"domain={domain} attempt={attempt}")
            self.case.status = CaseStatus.diagnosing
            self._persist()

            logger.info("Routing to %s subagent for %s (attempt=%d)", domain, self.case.jira_ticket_id, attempt)
            diagnosis = await subagent.diagnose()
            self.case.reroute_guard.add(domain)

            # Merge diagnosis back into the Case
            _apply_diagnosis_to_case(self.case, diagnosis)

            if diagnosis.next_action == NextAction.propose_to_human:
                await self._validate_and_approve(diagnosis)
                return

            if diagnosis.next_action == NextAction.reroute:
                new_domain = diagnosis.reroute_target
                if not new_domain:
                    await self._escalate(
                        f"{domain} subagent bounced back without a reroute target",
                    )
                    return
                self.case.owning_domain = new_domain
                self.case.append_trail(domain, "bounced_back", notes=f"suggests={new_domain}")
                logger.info("%s → rerouting to %s", domain, new_domain)
                continue

            if diagnosis.next_action == NextAction.escalate:
                await self._escalate(
                    f"{domain} subagent escalated: {diagnosis.notes or '(no detail)'}",
                    hypotheses=[diagnosis.notes] if diagnosis.notes else [],
                )
                return

            if diagnosis.next_action == NextAction.need_info:
                # Relay is handled inside the subagent when dialect is injected.
                # need_info returned here means the subagent is still blocked → escalate.
                await self._escalate(
                    f"Human relay needed from {domain}: {diagnosis.needs_from_human}",
                    hypotheses=[diagnosis.needs_from_human or ""],
                )
                return

        # Exhausted reroutes without resolution
        await self._escalate(
            f"Max reroutes ({MAX_REROUTES}) reached without diagnosis",
            hypotheses=list(self.case.alternative_hypotheses),
        )

    async def _write_resolution(self) -> None:
        """Persist resolution status and write the resolution record back to Jira."""
        if self.case.status == CaseStatus.escalated:
            return
        self.case.status = CaseStatus.resolved
        self._persist()

        diagnosis_summary = self.case.resolution_summary or "(no diagnosis summary)"
        fix_parts: list[str] = []
        if self.case.proposed_fix:
            fix_parts.append(self.case.proposed_fix.get("summary", ""))
            if sql := self.case.proposed_fix.get("sql_statement"):
                fix_parts.append(f"SQL: {sql}")
            if verify := self.case.proposed_fix.get("verification"):
                fix_parts.append(f"Verify: {verify}")
        fix_summary = " | ".join(p for p in fix_parts if p) or "(no fix summary)"

        if self._jira_client is not None:
            from support_orchestration.glue.jira import write_resolution as _jira_write
            await _jira_write(
                self.case, diagnosis_summary, fix_summary,
                jira_client=self._jira_client,
            )

        if self._vector is not None:
            try:
                memory_summary = await self._formulate_memory(diagnosis_summary, fix_summary)
                await self._vector.write({
                    "jira_id":     self.case.jira_ticket_id,
                    "client_id":   self.case.client,
                    "entity_type": self.case.entity_type,
                    "domain":      self.case.owning_domain,
                    "summary":     memory_summary,
                    "root_cause":  self.case.resolution_summary,
                    "fix_summary": fix_summary,
                })
            except Exception:
                logger.warning(
                    "Vector write-back failed for %s (non-fatal)",
                    self.case.jira_ticket_id,
                    exc_info=True,
                )

        logger.info(
            "RESOLUTION for %s: %s",
            self.case.jira_ticket_id,
            diagnosis_summary,
        )

    # ── Human dialogue helpers ────────────────────────────────────────────────

    async def _validate_and_approve(self, diagnosis: Diagnosis) -> None:
        """Send /validate → /approve and handle engineer responses."""
        conclusion = (
            f"Root cause: {diagnosis.root_cause}. "
            f"Stuck at: {diagnosis.stuck_transition}. "
            f"Confidence: {diagnosis.confidence:.0%}."
        )

        self.case.status = CaseStatus.awaiting_human
        self._persist()

        await self._dialect.send("/validate", conclusion)
        reply = await self._dialect.receive()

        # C6 Haiku interprets reply; fall back to regex when no API client
        if self._anthropic is not None:
            intent = await c6_interpret_reply(reply, conclusion, self._anthropic)
            rejected = (intent == "reject")
        else:
            rejected = _is_rejection(reply)

        if rejected:
            self.case.append_trail("orchestrator", "human_rejected_validation", notes=reply[:200])
            await self._escalate(
                f"Engineer rejected conclusion: {reply[:200]}",
                hypotheses=[],
            )
            return

        # Build fix message for /approve
        if diagnosis.proposed_fix:
            fix = diagnosis.proposed_fix
            fix_lines = [fix.summary]
            if fix.sql_statement:
                fix_lines.append(f"SQL: {fix.sql_statement}")
            fix_lines.append(f"Verify: {fix.verification}")
            fix_msg = " | ".join(fix_lines)
        else:
            fix_msg = diagnosis.root_cause

        self.case.status = CaseStatus.fix_proposed
        self._persist()

        await self._dialect.approve(fix_msg)

        self.case.resolution_summary = diagnosis.root_cause
        self.case.append_trail("orchestrator", "fix_approved")
        self._persist()

    async def _escalate(self, reason: str, hypotheses: list[str] | None = None) -> None:
        """Set case to escalated, notify engineer, persist."""
        self.case.status = CaseStatus.escalated
        self.case.append_trail("orchestrator", "escalated", notes=reason)

        msg = f"Cannot auto-diagnose. {reason}"
        if hypotheses:
            msg += f" | Hypotheses: {'; '.join(h for h in hypotheses if h)}"
        await self._dialect.send_info(msg)
        self._persist()
        logger.warning("ESCALATED %s: %s", self.case.jira_ticket_id, reason)

    # ── Memory formulation (Haiku) ────────────────────────────────────────────

    async def _formulate_memory(self, diagnosis_summary: str, fix_summary: str) -> str:
        """Haiku-formulated memory record for pgvector write-back.

        Produces a structured four-line Markdown block that embeds better than
        raw diagnosis text. Falls back to raw diagnosis_summary when no API client.
        """
        if self._anthropic is None:
            return diagnosis_summary

        user_content = (
            f"Domain: {self.case.owning_domain}\n"
            f"Entity: {self.case.entity_type} {self.case.entity_id}\n"
            f"Diagnosis: {diagnosis_summary}\n"
            f"Fix applied: {fix_summary}"
        )
        try:
            resp = await self._anthropic.messages.create(
                model=MODEL_MEMORY,
                max_tokens=200,
                system=_MEMORY_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
            )
            return resp.content[0].text if resp.content else diagnosis_summary
        except Exception as e:
            logger.warning("Memory formulation failed for %s: %s", self.case.jira_ticket_id, e)
            return diagnosis_summary

    # ── C8 dossier (Sonnet) ───────────────────────────────────────────────────

    async def _build_c8_dossier(self) -> str:
        """C8 — Sonnet warm-start dossier build."""
        from support_orchestration.orchestrator.prompts import C8_SYSTEM

        try:
            resp = await self._anthropic.messages.create(
                model=MODEL_TRIAGE,
                max_tokens=600,
                system=[{
                    "type": "text",
                    "text": C8_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": render_case_for_triage(self.case)}],
            )
            return resp.content[0].text if resp.content else render_warm_start_dossier(self.case)
        except Exception as e:
            logger.warning("C8 dossier LLM call failed for %s: %s", self.case.jira_ticket_id, e)
            return render_warm_start_dossier(self.case)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save_case(self.case)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _apply_diagnosis_to_case(case: Case, diagnosis: Diagnosis) -> None:
    """Merge diagnosis fields back into the accumulating Case object."""
    patch = diagnosis.to_case_patch()
    for key, val in patch.items():
        if val is not None:
            setattr(case, key, val)
    for ref in diagnosis.evidence_refs:
        case.add_evidence(source=ref)


_REJECTION_RE = re.compile(r"^(no|nope|disagree|wrong|incorrect)\b", re.IGNORECASE)


def _is_rejection(reply: str) -> bool:
    """Heuristic: did the engineer reject the conclusion? (C6 Haiku refinement in Prompt 7)"""
    return bool(_REJECTION_RE.match(reply.strip()))
