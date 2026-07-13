"""Background prep runner — C7 in docs/4 §4.2.

Runs while an incident is unassigned so the orchestrator starts warm:
  1. phoenix_resolve  — connectivity tier + log posture (no LLM)
  2. entity classify  — Haiku via Batch API (50% off) extracts entity/state from ticket text
  3. history_search   — vector search for prior similar incidents
  4. lifecycle locate — deterministic map lookup once entity type + state are known

All steps run in parallel; each is isolated so one failure doesn't abort the rest.
Results are written back into the Case object and persisted to the state store.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from support_orchestration.models import Case, ConnectivityTier
from support_orchestration.orchestrator.triage import find_transition, load_lifecycle_map
from support_orchestration.tools.history_retrieval import history_search
from support_orchestration.tools.mcp_server import StubVectorAdapter, VectorStoreAdapter
from support_orchestration.tools.phoenix_resolver import phoenix_resolve

logger = logging.getLogger(__name__)

MODEL_HAIKU = "claude-haiku-4-5"

_CLASSIFY_SYSTEM = (
    "You extract structured entity information from warehouse support ticket text. "
    "Return ONLY valid JSON, no prose: "
    '{"entity_type": "order"|"tote"|"bin"|null, '
    '"entity_id": <string or null>, '
    '"entity_current_state": <string or null>}'
)

_BATCH_POLL_INTERVAL_SECONDS = 30
_BATCH_MAX_POLLS = 60  # 30 min max wait


class BackgroundPrepRunner:
    """
    Runs C7 background prep while an incident is unassigned.

    Inject anthropic_client=None to skip LLM steps (tests, CI without API key).
    Inject a stub VectorStoreAdapter for offline tests.
    """

    def __init__(
        self,
        *,
        state_store: Any,                    # CaseStore — avoids circular import
        anthropic_client: Any | None = None, # anthropic.AsyncAnthropic
        vector_adapter: VectorStoreAdapter | None = None,
    ) -> None:
        self._store = state_store
        self._anthropic = anthropic_client
        self._vector = vector_adapter or StubVectorAdapter()

    async def prepare(self, case: Case) -> None:
        """Run all background prep in parallel, then persist the updated Case."""
        logger.info("PREP starting for %s (client=%s)", case.jira_ticket_id, case.client)
        await asyncio.gather(
            self._safe(self._resolve_connectivity(case), "connectivity"),
            self._safe(self._classify_entity(case), "entity_classify"),
            self._safe(self._search_history(case), "history_search"),
        )
        self._store.save_case(case)
        logger.info("PREP complete for %s", case.jira_ticket_id)

    # ── Steps ──────────────────────────────────────────────────────────────────

    async def _resolve_connectivity(self, case: Case) -> None:
        result = await phoenix_resolve(case.client)
        tier_str = result.get("connectivity_tier", "human_relay")
        try:
            case.connectivity_tier = ConnectivityTier(tier_str)
        except ValueError:
            case.connectivity_tier = ConnectivityTier.human_relay
        case.log_posture = result.get("log_posture", "human_relay")

    async def _classify_entity(self, case: Case) -> None:
        if self._anthropic is None:
            return

        classification = await self._classify_via_batch_api(case)
        if not classification:
            return

        case.entity_type = classification.get("entity_type")
        case.entity_id = classification.get("entity_id")
        case.entity_current_state = classification.get("entity_current_state")

        if case.entity_type and case.entity_current_state:
            self._locate_lifecycle(case)

    async def _search_history(self, case: Case) -> None:
        query = " ".join(filter(None, [
            case.entity_type,
            "stuck",
            case.entity_current_state,
            case.description[:100] if case.description else None,
        ]))
        hits = await history_search(
            client_id=case.client,
            query=query or case.jira_ticket_id,
            vector=self._vector,
            top_k=5,
        )
        for hit in hits:
            case.add_evidence(
                source=f"history:{hit.get('jira_id', 'unknown')}",
                summary=hit.get("summary", ""),
            )

    # ── Batch API classification ────────────────────────────────────────────────

    async def _classify_via_batch_api(self, case: Case) -> dict[str, str | None] | None:
        """Submit entity classification to Haiku via Batch API (50% off)."""
        client = self._anthropic
        if client is None:
            return None
        prompt = self._build_classify_prompt(case)

        try:
            batch = await client.beta.messages.batches.create(
                requests=[{
                    "custom_id": f"classify-{case.case_id}",
                    "params": {
                        "model": MODEL_HAIKU,
                        "max_tokens": 300,
                        "system": _CLASSIFY_SYSTEM,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                }]
            )
        except Exception as e:
            logger.warning("Batch API submit failed for %s: %s", case.jira_ticket_id, e)
            return None

        # Poll until ended
        for _ in range(_BATCH_MAX_POLLS):
            try:
                status = await client.beta.messages.batches.retrieve(batch.id)
                if status.processing_status == "ended":
                    break
            except Exception as e:
                logger.warning("Batch API poll failed: %s", e)
                return None
            await asyncio.sleep(_BATCH_POLL_INTERVAL_SECONDS)
        else:
            logger.warning("Batch API timed out for %s", case.jira_ticket_id)
            return None

        # Parse result
        try:
            async for result in client.beta.messages.batches.results(batch.id):
                if result.result.type == "succeeded":
                    content = result.result.message.content
                    text = content[0].text if content else ""
                    start = text.find("{")
                    end = text.rfind("}") + 1
                    if 0 <= start < end:
                        parsed: dict[str, str | None] = json.loads(text[start:end])
                        return parsed
        except Exception as e:
            logger.warning("Batch API result parse failed for %s: %s", case.jira_ticket_id, e)

        return None

    def _build_classify_prompt(self, case: Case) -> str:
        parts = [f"Ticket: {case.jira_ticket_id}", f"Priority: {case.priority.value}"]
        if case.description:
            parts.append(f"Description: {case.description[:500]}")
        return "\n".join(parts)

    def _locate_lifecycle(self, case: Case) -> None:
        if not case.entity_type or not case.entity_current_state:
            return
        try:
            map_data = load_lifecycle_map(case.entity_type, case.client)
            transition = find_transition(map_data, case.entity_current_state)
            if transition:
                case.lifecycle_slice = {
                    "from": transition["from"],
                    "to": transition.get("to"),
                    "owning_domain": transition.get("owning_domain"),
                    "blocker_classes": transition.get("blocker_classes", []),
                }
                case.stuck_transition = f"{transition['from']} → {transition.get('to', '?')}"
                case.owning_domain = transition.get("owning_domain")
        except Exception:
            logger.debug("Lifecycle locate skipped for %s", case.jira_ticket_id)

    @staticmethod
    async def _safe(coro: Any, name: str) -> None:
        try:
            await coro
        except Exception as e:
            logger.warning("Background prep step %r failed: %s", name, e)
