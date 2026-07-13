"""Smoke tests for the eval harness — run it inside pytest so it can never
silently break while the unit suite stays green (see: FixtureVectorAdapter
missing write() after the RAG write-back added it to the VectorStoreAdapter ABC).

These tests exercise the same entry points as `python -m evals`:
  - run_all_evals()                  (triage, no LLM)
  - validate_all_fixture_adapters()  (adapter wiring, no LLM)
  - run_eval(anthropic_client=...)   (diagnosis path, mock LLM)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from evals.adapters import FixtureVectorAdapter, build_fixture_adapters
from evals.harness import (
    load_fixtures,
    run_all_evals,
    run_eval,
    validate_all_fixture_adapters,
)

EXPECTED_MIN_FIXTURES = 18


# ── Harness entry points (no LLM) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_all_evals_triage_smoke() -> None:
    """The full triage eval must run end-to-end at 100% accuracy."""
    agg = await run_all_evals()
    assert agg["total"] >= EXPECTED_MIN_FIXTURES
    assert agg["triage_accuracy_pct"] == 100.0
    # Without an anthropic_client every fixture skips diagnosis — none errored out.
    assert agg["diagnosis_skipped_count"] == agg["total"]


@pytest.mark.asyncio
async def test_validate_all_fixture_adapters_smoke() -> None:
    """Adapter wiring validation must pass for every fixture."""
    agg = await validate_all_fixture_adapters()
    assert agg["total"] >= EXPECTED_MIN_FIXTURES
    assert agg["failed"] == 0
    assert agg["no_mocked_responses"] == 0


def test_all_fixture_adapters_instantiable() -> None:
    """Every fixture's mocked_tool_responses must build concrete adapters.

    Guards against an adapter ABC gaining an abstract method that a Fixture*
    adapter doesn't implement (TypeError at instantiation).
    """
    fixtures = load_fixtures()
    assert len(fixtures) >= EXPECTED_MIN_FIXTURES
    for fixture in fixtures:
        adapters = build_fixture_adapters(fixture)
        assert set(adapters) == {"db", "log", "vector", "github", "phoenix"}


@pytest.mark.asyncio
async def test_fixture_vector_adapter_write_records() -> None:
    adapter = FixtureVectorAdapter([])
    await adapter.write({"jira_id": "WH-1", "summary": "s"})
    assert adapter.written == [{"jira_id": "WH-1", "summary": "s"}]


# ── Diagnosis path with a mock LLM ────────────────────────────────────────────

def _end_turn_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _diagnosis_text_from_fixture(fixture: dict[str, Any]) -> str:
    """Build a <diagnosis> block that exactly matches the fixture's scoring criteria."""
    inp = fixture["input"]
    scoring = fixture["scoring"]
    dcif = scoring["diagnosis_correct_if"]
    keywords: list[str] = scoring["fix_match_if"]["proposed_fix_must_mention"]
    d = {
        "entity": {
            "type": inp["entity_type"],
            "id": inp["entity_id"],
            "current_state": inp["entity_current_state"],
        },
        "stuck_transition": dcif["stuck_transition"],
        "owning_domain": fixture["ground_truth"]["owning_domain"],
        "root_cause": fixture["ground_truth"]["root_cause"],
        "blocker_class": dcif["blocker_class"],
        "dependency_findings": [],
        "proposed_fix": {
            "summary": f"Apply fix involving {', '.join(keywords)}.",
            "human_steps": ["Apply the fix", "Verify the entity advances"],
            "sql_statement": fixture["ground_truth"].get("fix_sql"),
            "reversible": True,
            "verification": "Entity advances to the next state.",
        },
        "confidence": 0.9,
        "evidence_refs": ["db:fixture"],
        "needs_from_human": None,
        "next_action": "propose_to_human",
        "reroute_target": None,
        "notes": "",
    }
    return f"<diagnosis>\n{json.dumps(d)}\n</diagnosis>"


@pytest.mark.asyncio
async def test_run_eval_diagnosis_path_with_mock_llm() -> None:
    """run_eval with an anthropic_client must score diagnosis + fix-match.

    Proves the full mocked pipeline: fixture adapters → subagent tool loop →
    diagnosis parsing → scoring, without any external connectivity.
    """
    fixtures = [f for f in load_fixtures() if f["fixture_id"] == "wes_order_lost_ack_01"]
    assert fixtures, "expected fixture wes_order_lost_ack_01 to exist"
    fixture = fixtures[0]

    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        return_value=_end_turn_response(_diagnosis_text_from_fixture(fixture))
    )

    result = await run_eval(fixture, anthropic_client=client)
    assert result["triage_accuracy"] is True
    assert result["diagnosis_skipped"] is False
    assert result["diagnosis_correct"] is True
    assert result["fix_match"] is True
