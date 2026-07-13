"""System prompts and context renderers for orchestrator model calls.

Prompt-cached blocks: SYSTEM_ORCHESTRATOR + lifecycle map text.
Per-incident content: render_case_for_triage, render_warm_start_dossier.

docs/4 §4.3–4.4: stable prefix cached at ~10% of input cost; only the
per-incident Case slice is fresh tokens each call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from support_orchestration.models import Case

SYSTEM_ORCHESTRATOR = """\
You are a warehouse automation support orchestrator. Triage the incident and decide \
which domain subagent should diagnose it.

DECISION METHOD (in order):
1. Find the entity's stuck transition in the lifecycle map to identify the owning domain.
2. Weigh history-match evidence (if present) to adjust confidence.
3. Apply the escalate-vs-probe rule:
   • confidence ≥ 0.70  → "route"     (single domain dominates)
   • 0.40 < conf < 0.70 → "probe"     (ambiguous; cheap decisive probe possible)
   • confidence ≤ 0.40  → "escalate"  (novel or highly ambiguous)

INVARIANTS — never violate:
• Agents read, reason, recommend. NEVER suggest writing to any client system.
• All conclusions are scoped to the incident's client — no cross-client reasoning.

Output ONLY valid JSON with no prose before or after:
{
  "owning_domain": "<WES|GTP_PICKING|GTP_DECANT|IMS|ASRS|LPN|WCS|infra|ESB|null>",
  "confidence": <float 0.0–1.0>,
  "stuck_transition": "<from_state> → <to_state> or null",
  "hypothesis": "<one-sentence primary hypothesis or null>",
  "alternative_hypotheses": ["<alt1>", ...],
  "next_action": "route | probe | escalate"
}"""

C1_SYSTEM = (
    "Extract entity information from a warehouse support ticket. "
    "Return ONLY valid JSON, no prose: "
    '{"entity_type": "order"|"tote"|"bin"|null, '
    '"entity_id": <string|null>, '
    '"entity_current_state": <string|null>}'
)

C8_SYSTEM = (
    "Summarize a warehouse support incident as a concise warm-start dossier for the "
    "assigned engineer. Include: entity + current state, SLA urgency, preliminary "
    "hypothesis, connectivity tier, and any prior similar incidents. "
    "5–8 bullet points, plain text, no markdown headers."
)


def render_lifecycle_map_text(map_data: dict[str, Any]) -> str:
    """Serialize a lifecycle map to a compact text block (prompt-cached)."""
    entity = map_data.get("entity", "unknown").upper()
    lines = [f"LIFECYCLE MAP — {entity} entity"]
    for t in map_data.get("transitions", []):
        blockers = [b.get("class", "?") for b in t.get("blocker_classes", [])]
        ims_flag = " [IMS-CHECK-FIRST]" if t.get("ims_check_first") else ""
        lines.append(
            f"  {t['from']} → {t.get('to', '?')}"
            f" | owner={t.get('owning_domain')}"
            f"{ims_flag}"
            f" | blockers={blockers}"
        )
    return "\n".join(lines)


def render_case_for_triage(case: Case) -> str:
    """Build the per-incident user message for C3 triage (never cached)."""
    parts = [
        f"Ticket: {case.jira_ticket_id}",
        f"Client: {case.client}",
        f"Priority: {case.priority.value}",
        f"SLA deadline: {case.sla_deadline.isoformat()}",
    ]
    if case.assignee_email:
        parts.append(f"Assignee: {case.assignee_email}")
    if case.entity_type:
        parts.append(f"Entity type: {case.entity_type}")
    if case.entity_id:
        parts.append(f"Entity ID: {case.entity_id}")
    if case.entity_current_state:
        parts.append(f"Entity current state: {case.entity_current_state}")
    if case.description:
        parts.append(f"Ticket description: {case.description[:500]}")
    history = [e.summary for e in case.evidence if e.source.startswith("history:") and e.summary]
    if history:
        parts.append(f"History matches: {'; '.join(history[:3])}")
    if case.lifecycle_slice:
        parts.append(f"Lifecycle slice from background prep: {case.lifecycle_slice}")
    return "\n".join(parts)


def render_warm_start_dossier(case: Case) -> str:
    """Template-render a warm-start dossier (used when no Anthropic client is available)."""
    lines: list[str] = [
        f"Incident {case.jira_ticket_id} — {case.client} — {case.priority.value}",
        f"SLA deadline: {case.sla_deadline.isoformat()}",
    ]
    if case.entity_type and case.entity_id:
        lines.append(
            f"Entity: {case.entity_type} {case.entity_id}"
            + (f" @ {case.entity_current_state}" if case.entity_current_state else "")
        )
    if case.stuck_transition:
        lines.append(f"Stuck transition: {case.stuck_transition}")
    if case.owning_domain:
        lines.append(f"Likely owner: {case.owning_domain}")
    if case.connectivity_tier:
        lines.append(f"Access tier: {case.connectivity_tier.value}")
    history = [e.summary for e in case.evidence if e.source.startswith("history:") and e.summary]
    if history:
        lines.append(f"Prior similar incidents: {'; '.join(history[:2])}")
    if case.description:
        lines.append(f"Description: {case.description[:200]}")
    return "\n".join(lines)
