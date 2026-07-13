"""Triage logic — locate entity on lifecycle map, compute routing decision.

Model calls: C1 (Haiku, classify entity from ticket text if not already set),
             C3 (Sonnet, route with confidence, prompt-cached).
Lifecycle map loaded from maps/base/<entity>.yaml + client delta (if any).

docs/4 §4.2–4.4: system prompt + map are prompt-cached; only the per-incident
Case slice is fresh tokens each call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from support_orchestration.models import Case

from support_orchestration.config.base import MODEL_HAIKU, MODEL_SONNET

logger = logging.getLogger(__name__)

MAPS_BASE_DIR = Path(__file__).parents[2] / "maps" / "base"

# Confidence thresholds for the escalate-vs-probe rule (docs/3 §3.4)
CONF_ROUTE   = 0.70   # ≥ this → route
CONF_PROBE   = 0.40   # between this and CONF_ROUTE → probe (if cheap probe exists)
# below CONF_PROBE → escalate


class TriageDecision(str, Enum):
    route    = "route"
    probe    = "probe"
    escalate = "escalate"


@dataclass
class TriageResult:
    """Structured output of the C1→C3 triage pipeline."""
    owning_domain: str | None
    confidence: float
    stuck_transition: str | None
    hypothesis: str | None
    alternative_hypotheses: list[str] = field(default_factory=list)
    next_action: TriageDecision = TriageDecision.escalate
    reasoning: str = ""


# ── Lifecycle map loading ─────────────────────────────────────────────────────

def load_lifecycle_map(entity_type: str, client_id: str | None = None) -> dict[str, Any]:
    """Load the base lifecycle map, optionally deep-merged with a client delta."""
    base_path = MAPS_BASE_DIR / f"{entity_type}.yaml"
    if not base_path.exists():
        raise FileNotFoundError(f"No lifecycle map for entity '{entity_type}' at {base_path}")
    with base_path.open() as f:
        base: dict[str, Any] = yaml.safe_load(f)

    if client_id:
        delta_path = (
            Path(__file__).parents[2] / "maps" / "clients" / client_id
            / f"{entity_type}_deltas.yaml"
        )
        if delta_path.exists():
            with delta_path.open() as f:
                delta: dict[str, Any] = yaml.safe_load(f) or {}
            if delta:
                base = _delta_merge(base, delta)

    return base


def find_transition(map_data: dict[str, Any], current_state: str) -> dict[str, Any] | None:
    """Find the transition the entity should have taken from current_state."""
    transitions: list[dict[str, Any]] = map_data.get("transitions", [])
    for transition in transitions:
        if transition["from"] == current_state:
            return transition
    return None


def _delta_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a client delta into a base map. Dicts recurse; other types replace."""
    result = dict(base)
    for key, val in delta.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _delta_merge(result[key], val)
        else:
            result[key] = val
    return result


# ── Triage pipeline ───────────────────────────────────────────────────────────

async def run_triage(case: Case, anthropic_client: Any | None) -> TriageResult:
    """Entry point: C1 (optional) + C3 (LLM or deterministic fallback).

    If ``anthropic_client`` is None, falls back to deterministic map lookup —
    used in tests and offline operation.
    """
    # SLA-tight override — escalate immediately so a human has runway
    if case.is_sla_tight():
        return TriageResult(
            owning_domain=case.owning_domain,
            confidence=case.confidence or 0.50,
            stuck_transition=case.stuck_transition,
            hypothesis=case.hypothesis or "SLA tight — escalating immediately",
            alternative_hypotheses=list(case.alternative_hypotheses),
            next_action=TriageDecision.escalate,
            reasoning="SLA < 15 min — handing to human with best available hypotheses",
        )

    if anthropic_client is None:
        return _deterministic_triage(case)

    return await _llm_triage(case, anthropic_client)


# ── Deterministic triage (map-only, no LLM) ──────────────────────────────────

def _deterministic_triage(case: Case) -> TriageResult:
    """Map-lookup-only triage. Used in tests and when no Anthropic client is available."""
    # Background prep may have already set owning_domain + stuck_transition
    if case.owning_domain and case.stuck_transition:
        return TriageResult(
            owning_domain=case.owning_domain,
            confidence=0.85,
            stuck_transition=case.stuck_transition,
            hypothesis=case.hypothesis or f"Entity stuck at {case.stuck_transition}",
            alternative_hypotheses=list(case.alternative_hypotheses),
            next_action=TriageDecision.route,
        )

    if case.entity_type and case.entity_current_state:
        try:
            map_data = load_lifecycle_map(case.entity_type, case.client)
            transition = find_transition(map_data, case.entity_current_state)
            if transition:
                owning = transition.get("owning_domain")
                stuck = f"{transition['from']} → {transition.get('to', '?')}"
                blockers = [b.get("class", "?") for b in transition.get("blocker_classes", [])]
                return TriageResult(
                    owning_domain=owning,
                    confidence=0.85,
                    stuck_transition=stuck,
                    hypothesis=f"Stuck at {stuck}; candidate blockers: {blockers}",
                    next_action=TriageDecision.route,
                )
        except Exception:
            pass  # fall through to escalate

    return TriageResult(
        owning_domain=None,
        confidence=0.20,
        stuck_transition=None,
        hypothesis=None,
        next_action=TriageDecision.escalate,
        reasoning="Insufficient information to determine owning domain",
    )


# ── LLM triage (C1 + C3) ─────────────────────────────────────────────────────

async def _llm_triage(case: Case, anthropic_client: Any) -> TriageResult:
    """C1 (Haiku entity classify if needed) + C3 (Sonnet route decision)."""
    from support_orchestration.orchestrator.prompts import (
        C1_SYSTEM,
        SYSTEM_ORCHESTRATOR,
        render_case_for_triage,
        render_lifecycle_map_text,
    )

    # C1 — skip if background prep already classified the entity
    if not case.entity_type or not case.entity_current_state:
        classification = await _c1_classify(case, anthropic_client, C1_SYSTEM)
        if classification:
            case.entity_type = classification.get("entity_type") or case.entity_type
            case.entity_id = classification.get("entity_id") or case.entity_id
            case.entity_current_state = (
                classification.get("entity_current_state") or case.entity_current_state
            )

    # Build lifecycle map text for C3 caching
    map_text = ""
    if case.entity_type:
        try:
            map_data = load_lifecycle_map(case.entity_type, case.client)
            map_text = render_lifecycle_map_text(map_data)
        except FileNotFoundError:
            pass

    # C3 — Sonnet triage/route (prompt-cached system + map)
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_ORCHESTRATOR,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if map_text:
        system_blocks.append({
            "type": "text",
            "text": map_text,
            "cache_control": {"type": "ephemeral"},
        })

    try:
        resp = await anthropic_client.messages.create(
            model=MODEL_SONNET,
            max_tokens=500,
            system=system_blocks,
            messages=[{"role": "user", "content": render_case_for_triage(case)}],
        )
        text = resp.content[0].text if resp.content else ""
        result = _parse_triage_json(text)
        logger.info(
            "C3 triage for %s: domain=%s confidence=%.2f action=%s",
            case.jira_ticket_id, result.owning_domain, result.confidence, result.next_action,
        )
        return result
    except Exception as e:
        logger.warning(
            "C3 triage LLM call failed for %s: %s — falling back to deterministic",
            case.jira_ticket_id, e,
        )
        return _deterministic_triage(case)


async def _c1_classify(
    case: Case, anthropic_client: Any, system: str
) -> dict[str, str | None] | None:
    """C1 — single-shot Haiku entity classification."""
    if not case.description:
        return None
    prompt = f"Ticket: {case.jira_ticket_id}\nDescription: {case.description[:500]}"
    try:
        resp = await anthropic_client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        start, end = text.find("{"), text.rfind("}") + 1
        if 0 <= start < end:
            parsed: dict[str, str | None] = json.loads(text[start:end])
            return parsed
    except Exception as e:
        logger.warning("C1 entity classification failed for %s: %s", case.jira_ticket_id, e)
    return None


def _parse_triage_json(text: str) -> TriageResult:
    """Parse C3 JSON output into a TriageResult; escalate on any parse failure."""
    start, end = text.find("{"), text.rfind("}") + 1
    if 0 <= start < end:
        try:
            data = json.loads(text[start:end])
            next_action_raw = data.get("next_action", "escalate")
            try:
                next_action = TriageDecision(next_action_raw)
            except ValueError:
                next_action = TriageDecision.escalate
            return TriageResult(
                owning_domain=data.get("owning_domain"),
                confidence=float(data.get("confidence", 0.5)),
                stuck_transition=data.get("stuck_transition"),
                hypothesis=data.get("hypothesis"),
                alternative_hypotheses=list(data.get("alternative_hypotheses") or []),
                next_action=next_action,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Failed to parse C3 triage JSON: %s — text=%r", e, text[:200])

    return TriageResult(
        owning_domain=None,
        confidence=0.0,
        stuck_transition=None,
        hypothesis=None,
        next_action=TriageDecision.escalate,
        reasoning="Failed to parse LLM triage response",
    )
