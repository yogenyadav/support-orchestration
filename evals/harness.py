"""Eval harness — replay past incidents and score triage + diagnosis + fix-match.

docs/4-technical-build.md §4.7.

Fixture format: YAML files under evals/fixtures/base/ (and per-client subdirs).
Each fixture is one past resolved incident with known ground truth.

Scoring:
  - triage_accuracy:    did it route to the correct domain? (YAML map lookup, no LLM)
  - diagnosis_correct:  did it find the right stuck_transition and blocker_class?
  - fix_match:          does proposed_fix.summary mention required keywords? (fuzzy)

Triage eval is fully functional now. Diagnosis + fix-match activate in Prompt 7
when subagent.diagnose() is implemented — until then both return None and the
diagnosis_skipped flag is set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from support_orchestration.models import Case, ConnectivityTier, Diagnosis, Priority
from support_orchestration.models.case import CaseStatus
from support_orchestration.orchestrator.triage import find_transition, load_lifecycle_map
from support_orchestration.subagents.base import get_subagent

from .adapters import build_fixture_adapters

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_SLA_HOURS: dict[str, int] = {
    "P1": 4,
    "P2": 8,
    "P3": 72,
    "P4": 168,
}


def load_fixtures(domain: str | None = None) -> list[dict[str, Any]]:
    """Load all fixture YAML files, optionally filtered to one owning domain."""
    fixtures = []
    for path in sorted(FIXTURES_DIR.rglob("*.yaml")):
        with path.open() as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        if domain and data.get("ground_truth", {}).get("owning_domain") != domain:
            continue
        fixtures.append(data)
    return fixtures


def _case_from_fixture(inp: dict[str, Any]) -> Case:
    priority = Priority(inp["priority"])
    created_at = datetime.now(timezone.utc)
    sla_deadline = created_at + timedelta(hours=_SLA_HOURS[priority.value])
    return Case(
        jira_ticket_id=inp["jira_ticket_id"],
        client=inp["client"],
        priority=priority,
        entity_type=inp["entity_type"],
        entity_id=inp["entity_id"],
        entity_current_state=inp["entity_current_state"],
        assignee_email="eval@harness",
        created_at=created_at,
        sla_deadline=sla_deadline,
        connectivity_tier=ConnectivityTier.human_relay,
        status=CaseStatus.triaging,
    )


def _score_diagnosis(diagnosis: Diagnosis, scoring: dict[str, Any]) -> bool:
    dcif = scoring["diagnosis_correct_if"]
    return bool(
        diagnosis.stuck_transition == dcif["stuck_transition"]
        and diagnosis.blocker_class == dcif["blocker_class"]
    )


def _score_fix(diagnosis: Diagnosis, scoring: dict[str, Any]) -> bool:
    keywords: list[str] = scoring["fix_match_if"]["proposed_fix_must_mention"]
    if diagnosis.proposed_fix is None:
        return False
    summary = diagnosis.proposed_fix.summary.lower()
    return all(kw.lower() in summary for kw in keywords)


async def run_eval(
    fixture: dict[str, Any],
    *,
    anthropic_client: Any = None,
) -> dict[str, Any]:
    """
    Replay one fixture through the system in shadow mode and return scores.

    anthropic_client: pass an AsyncAnthropic instance to activate diagnosis eval.
    Without it, diagnosis is skipped (same as before Prompt 7).

    Returns:
        {
            "fixture_id": str,
            "triage_accuracy": bool,
            "diagnosis_correct": bool | None,   # None = skipped (no anthropic_client)
            "fix_match": bool | None,
            "diagnosis_skipped": bool,
            "details": {...},
        }
    """
    fixture_id = fixture["fixture_id"]
    inp = fixture["input"]
    ground_truth = fixture["ground_truth"]
    scoring = fixture["scoring"]

    # Triage: pure YAML map lookup — no LLM, always scoreable
    map_data = load_lifecycle_map(inp["entity_type"], client_id=inp["client"])
    transition = find_transition(map_data, inp["entity_current_state"])
    predicted_domain = transition["owning_domain"] if transition else None
    triage_accuracy = predicted_domain == scoring["triage_correct_if"]["owning_domain"]

    # Case object needed for subagent construction
    case = _case_from_fixture(inp)
    case.owning_domain = predicted_domain

    # Diagnosis eval: skipped until Prompt 7 implements subagent.diagnose()
    diagnosis_correct: bool | None = None
    fix_match: bool | None = None
    diagnosis_skipped = False

    if predicted_domain:
        try:
            _adapters = build_fixture_adapters(fixture)
            subagent = get_subagent(
                predicted_domain,
                case,
                adapters=_adapters,
                anthropic_client=anthropic_client,
            )
            diagnosis: Diagnosis = await subagent.diagnose()
            diagnosis_correct = _score_diagnosis(diagnosis, scoring)
            fix_match = _score_fix(diagnosis, scoring)
        except NotImplementedError:
            diagnosis_skipped = True
    else:
        diagnosis_skipped = True  # no domain predicted — triage failed

    logger.info(
        "eval fixture=%s triage=%s diagnosis_skipped=%s",
        fixture_id,
        triage_accuracy,
        diagnosis_skipped,
    )

    return {
        "fixture_id": fixture_id,
        "triage_accuracy": triage_accuracy,
        "diagnosis_correct": diagnosis_correct,
        "fix_match": fix_match,
        "diagnosis_skipped": diagnosis_skipped,
        "details": {
            "predicted_domain": predicted_domain,
            "expected_domain": ground_truth["owning_domain"],
            "transition_found": transition is not None,
            "transition_id": transition.get("id") if transition else None,
        },
    }


async def validate_fixture_adapters(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Validate fixture adapter wiring without any LLM call.

    For each tool present in mocked_tool_responses, dispatches a simulated tool call
    through _dispatch_tool() and verifies the result has the expected shape and content.
    Also checks that history_search returns the correct blocker_class.

    Returns a per-fixture result dict with per-tool check outcomes.
    """
    fixture_id = fixture["fixture_id"]
    inp = fixture["input"]
    ground_truth = fixture["ground_truth"]
    mocked: dict[str, Any] = fixture.get("mocked_tool_responses", {})

    if not mocked:
        return {
            "fixture_id": fixture_id,
            "status": "no_mocked_responses",
            "checks": {},
            "all_ok": False,
        }

    # Build a subagent with fixture adapters — no anthropic_client, so diagnose() is never called.
    _adapters = build_fixture_adapters(fixture)
    case = _case_from_fixture(inp)
    case.owning_domain = ground_truth["owning_domain"]

    from support_orchestration.subagents.base import get_subagent
    subagent = get_subagent(ground_truth["owning_domain"], case, adapters=_adapters)

    checks: dict[str, dict[str, Any]] = {}

    async def _dispatch(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await subagent._dispatch_tool(tool, params)
            return {"ok": True, "result": result, "error": None}
        except Exception as exc:
            return {"ok": False, "result": None, "error": str(exc)}

    # phoenix_resolve
    if "phoenix_resolve" in mocked:
        r = await _dispatch("mcp__support__phoenix_resolve", {"client_id": inp["client"]})
        if r["ok"]:
            cfg = r["result"]
            r["ok"] = isinstance(cfg, dict) and "connectivity_tier" in cfg
            r["connectivity_tier"] = cfg.get("connectivity_tier") if r["ok"] else None
        checks["phoenix_resolve"] = r

    # db_state_read — entity_id must match; current_state must be non-null
    if "db_state_read" in mocked:
        r = await _dispatch("mcp__support__db_state_read", {
            "client_id": inp["client"],
            "entity_type": inp["entity_type"],
            "entity_id": inp["entity_id"],
        })
        if r["ok"]:
            res = r["result"]
            r["ok"] = (
                isinstance(res, dict)
                and res.get("found") is True
                and res.get("current_state") is not None
            )
            r["current_state"] = res.get("current_state") if isinstance(res, dict) else None
        checks["db_state_read"] = r

    # history_search — top result must have blocker_class matching ground_truth
    if "history_search" in mocked:
        r = await _dispatch("mcp__support__history_search", {
            "client_id": inp["client"],
            "query": inp["description"],
        })
        if r["ok"]:
            results = r["result"]
            top = results[0] if isinstance(results, list) and results else {}
            top_blocker = top.get("blocker_class")
            expected_blocker = ground_truth["blocker_class"]
            r["ok"] = bool(top_blocker)
            r["blocker_class_match"] = top_blocker == expected_blocker
            r["top_blocker_class"] = top_blocker
            r["expected_blocker_class"] = expected_blocker
        checks["history_search"] = r

    # log_read — content must be a non-empty string
    if "log_read" in mocked:
        r = await _dispatch("mcp__support__log_read", {
            "client_id": inp["client"],
            "query": "service error",
            "log_posture": "direct",
            "host": "fixture-host",
        })
        if r["ok"]:
            res = r["result"]
            content = res.get("content", "") if isinstance(res, dict) else ""
            r["ok"] = bool(content.strip())
            r["content_lines"] = len(content.splitlines())
        checks["log_read"] = r

    # github_read — only validated if explicitly mocked
    if "github_read" in mocked:
        gh_mocks = mocked["github_read"]
        sample_path = gh_mocks[0].get("path", "README.md") if gh_mocks else "README.md"
        r = await _dispatch("mcp__support__github_read", {
            "client_id": inp["client"],
            "path": sample_path,
        })
        if r["ok"]:
            r["ok"] = isinstance(r["result"], dict) and bool(r["result"].get("content", ""))
        checks["github_read"] = r

    all_ok = all(c["ok"] for c in checks.values())
    return {
        "fixture_id": fixture_id,
        "status": "ok" if all_ok else "FAIL",
        "checks": checks,
        "all_ok": all_ok,
    }


async def validate_all_fixture_adapters(
    domain: str | None = None,
) -> dict[str, Any]:
    """Validate adapter wiring for all fixtures (or one domain). No LLM calls."""
    fixtures = load_fixtures(domain)
    results = []
    for fixture in fixtures:
        result = await validate_fixture_adapters(fixture)
        results.append(result)

    total = len(results)
    passed = sum(1 for r in results if r["all_ok"])
    no_mocked = sum(1 for r in results if r["status"] == "no_mocked_responses")
    blocker_matches = sum(
        1 for r in results
        if r["checks"].get("history_search", {}).get("blocker_class_match", False)
    )
    blocker_total = sum(1 for r in results if "history_search" in r["checks"])

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed - no_mocked,
        "no_mocked_responses": no_mocked,
        "blocker_class_verified": blocker_matches,
        "blocker_class_total": blocker_total,
        "results": results,
    }


async def run_all_evals(
    domain: str | None = None,
    *,
    anthropic_client: Any = None,
) -> dict[str, Any]:
    """Run all fixtures and return an aggregate scorecard."""
    fixtures = load_fixtures(domain)
    results: list[dict[str, Any]] = []
    for fixture in fixtures:
        result = await run_eval(fixture, anthropic_client=anthropic_client)
        results.append(result)

    total = len(results)
    triage_correct = sum(1 for r in results if r["triage_accuracy"])
    diag_scored = [r for r in results if r["diagnosis_correct"] is not None]
    fix_scored = [r for r in results if r["fix_match"] is not None]
    skipped = sum(1 for r in results if r["diagnosis_skipped"])

    by_domain: dict[str, dict[str, int]] = {}
    for r in results:
        dom = r["details"]["predicted_domain"] or "unknown"
        if dom not in by_domain:
            by_domain[dom] = {
                "total": 0, "triage_correct": 0, "diagnosis_correct": 0, "fix_match": 0,
            }
        by_domain[dom]["total"] += 1
        if r["triage_accuracy"]:
            by_domain[dom]["triage_correct"] += 1
        if r["diagnosis_correct"]:
            by_domain[dom]["diagnosis_correct"] += 1
        if r["fix_match"]:
            by_domain[dom]["fix_match"] += 1

    return {
        "total": total,
        "triage_accuracy_pct": (triage_correct / total * 100) if total else 0.0,
        "diagnosis_correct_pct": (
            sum(1 for r in diag_scored if r["diagnosis_correct"]) / len(diag_scored) * 100
        ) if diag_scored else None,
        "fix_match_pct": (
            sum(1 for r in fix_scored if r["fix_match"]) / len(fix_scored) * 100
        ) if fix_scored else None,
        "diagnosis_skipped_count": skipped,
        "by_domain": by_domain,
        "results": results,
    }
