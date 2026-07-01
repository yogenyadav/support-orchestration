"""Case object — the single accumulating context that travels across the whole incident.

Defined in docs/3-agent-design.md §3.5. Every hop (orchestrator → subagent → reroute)
appends to this object; nothing is re-derived. Persisted to the state store so a new
orchestrator can rehydrate and resume after a crash.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Priority(str, Enum):
    P1 = "P1"   # 4-hour SLA
    P2 = "P2"   # 8-hour SLA
    P3 = "P3"   # 72-hour SLA
    P4 = "P4"   # 168-hour SLA


class ConnectivityTier(str, Enum):
    direct_connect = "direct_connect"   # DB/logs readable after human opens session
    human_relay = "human_relay"         # No prod access; human fetches on request
    s3_logs = "s3_logs"                 # Logs in AWS S3 {client} bucket


class CaseStatus(str, Enum):
    prepping = "prepping"           # Unassigned phase — background prep running
    triaging = "triaging"           # Assigned; orchestrator routing
    diagnosing = "diagnosing"       # Domain subagent working
    awaiting_human = "awaiting_human"   # One open request to engineer pending
    fix_proposed = "fix_proposed"   # Fix ready; awaiting human approval
    escalated = "escalated"         # Handed to human with ranked hypotheses
    resolved = "resolved"           # Human applied fix; Jira record written
    closed = "closed"               # Terminal


class EvidenceRef(BaseModel):
    """Pointer to a piece of evidence read during diagnosis."""
    source: str          # e.g. "db:orders", "log:wes-host", "jira:WH-1234"
    entity_id: str | None = None
    timestamp: datetime | None = None
    summary: str = ""


class DialogueTurn(BaseModel):
    """One turn of the agent ↔ engineer dialogue in Teams."""
    direction: str          # "agent_to_human" | "human_to_agent"
    verb: str | None = None # /info /ask /validate /approve /status (agent→human only)
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mirrored_to_jira: bool = False


class TrailEntry(BaseModel):
    """Audit trail — every routing decision and subagent visit."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str                  # "orchestrator" | "wes_subagent" | "ims_subagent" | ...
    action: str                 # "routed_to" | "bounced_back" | "escalated" | ...
    notes: str = ""


class Case(BaseModel):
    """The accumulating incident context. One instance per incident, persisted to state store."""

    # ── Identity ──────────────────────────────────────────────────────────────
    case_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    jira_ticket_id: str
    client: str                         # client identifier — scopes every tool call
    assignee_email: str | None = None   # populated when Jira 'assigned to' fires

    # ── SLA ───────────────────────────────────────────────────────────────────
    priority: Priority
    created_at: datetime                # Jira created timestamp — SLA clock starts here
    sla_deadline: datetime              # computed from priority + created_at

    # ── Client connectivity ───────────────────────────────────────────────────
    connectivity_tier: ConnectivityTier | None = None   # resolved by phoenix_resolver
    log_posture: str | None = None      # "direct" | "s3" | "human_relay"

    # ── Ticket text (from Jira summary + background) ─────────────────────────
    description: str = ""               # summary + background; used for entity classification

    # ── Triage / routing ──────────────────────────────────────────────────────
    entity_type: str | None = None      # "order" | "tote" | "bin" | ...
    entity_id: str | None = None
    entity_current_state: str | None = None
    stuck_transition: str | None = None # e.g. "prioritized → released"
    hypothesis: str | None = None       # primary hypothesis
    alternative_hypotheses: list[str] = Field(default_factory=list)
    owning_domain: str | None = None    # "WES" | "picking" | "WCS" | "IMS" | "ESB" | ...
    confidence: float | None = None

    # ── Evidence accumulated by subagents ─────────────────────────────────────
    evidence: list[EvidenceRef] = Field(default_factory=list)
    lifecycle_slice: dict[str, Any] = Field(default_factory=dict)   # relevant map excerpt

    # ── Human interaction ─────────────────────────────────────────────────────
    dialogue: list[DialogueTurn] = Field(default_factory=list)
    open_request: str | None = None     # exactly one open request at a time; None if idle

    # ── Fix ───────────────────────────────────────────────────────────────────
    proposed_fix: dict[str, Any] | None = None  # matches Diagnosis.proposed_fix shape
    fix_approved: bool = False
    fix_applied: bool = False

    # ── Routing trail ─────────────────────────────────────────────────────────
    trail: list[TrailEntry] = Field(default_factory=list)
    reroute_guard: set[str] = Field(default_factory=set)   # domains already visited

    # ── Status ────────────────────────────────────────────────────────────────
    status: CaseStatus = CaseStatus.prepping
    resolution_summary: str | None = None

    def append_trail(self, actor: str, action: str, notes: str = "") -> None:
        self.trail.append(TrailEntry(actor=actor, action=action, notes=notes))

    def add_evidence(self, source: str, entity_id: str | None = None,
                     timestamp: datetime | None = None, summary: str = "") -> None:
        self.evidence.append(EvidenceRef(
            source=source, entity_id=entity_id, timestamp=timestamp, summary=summary,
        ))

    def sla_seconds_remaining(self) -> float:
        return (self.sla_deadline - datetime.now(timezone.utc)).total_seconds()

    def is_sla_tight(self, threshold_seconds: int = 900) -> bool:
        """True when fewer than threshold_seconds remain on the SLA clock."""
        return self.sla_seconds_remaining() < threshold_seconds
