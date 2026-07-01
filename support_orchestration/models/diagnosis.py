"""Structured output contract for subagent diagnosis.

Shape defined in docs/4-technical-build.md §4.3. Every domain subagent returns
this schema. The orchestrator reads it to decide next_action.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NextAction(str, Enum):
    propose_to_human = "propose_to_human"
    reroute = "reroute"         # subagent says "not mine"; prefix domain: "reroute:IMS"
    escalate = "escalate"
    need_info = "need_info"     # blocked on human relay; one question pending


class ProposedFix(BaseModel):
    summary: str
    # Human-executable steps. Dominant pattern: one targeted DB UPDATE.
    human_steps: list[str]
    # SQL statement for the dominant fix pattern (targeted DB UPDATE).
    sql_statement: str | None = None
    reversible: bool
    verification: str           # what the engineer should observe to confirm success


class DependencyFinding(BaseModel):
    entity: str
    entity_id: str | None = None
    state: str
    healthy: bool
    notes: str = ""


class Diagnosis(BaseModel):
    """Output produced by a domain subagent at the end of its diagnosis loop."""

    entity: dict[str, str]          # {"type": "order", "id": "12345", "current_state": "prioritized"}
    stuck_transition: str           # "prioritized → released"
    owning_domain: str
    root_cause: str
    blocker_class: str              # references blocker_classes taxonomy in lifecycle maps

    dependency_findings: list[DependencyFinding] = Field(default_factory=list)
    proposed_fix: ProposedFix | None = None

    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)   # ["db:orders#12345@T1", ...]

    needs_from_human: str | None = None     # set when next_action == need_info
    next_action: NextAction = NextAction.propose_to_human

    # For reroute: the suggested target domain
    reroute_target: str | None = None

    # Free-form notes for the engineer (appears in Teams message)
    notes: str = ""

    def to_case_patch(self) -> dict[str, Any]:
        """Fields to merge back into the Case object after diagnosis."""
        return {
            "entity_type": self.entity.get("type"),
            "entity_id": self.entity.get("id"),
            "entity_current_state": self.entity.get("current_state"),
            "stuck_transition": self.stuck_transition,
            "owning_domain": self.owning_domain,
            "confidence": self.confidence,
            "proposed_fix": self.proposed_fix.model_dump() if self.proposed_fix else None,
        }
