"""Tests for JiraPoller — poll logic, background prep dispatch, orchestrator lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from support_orchestration.glue.jira import StubJiraClient
from support_orchestration.models import CaseStatus
from support_orchestration.storage.state_store import CaseStore
from support_orchestration.watcher.jira_poller import JiraPoller

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _ticket(jira_id: str = "WH-1", *, assigned: str | None = None) -> dict[str, Any]:
    return {
        "id": jira_id,
        "client": "acme",
        "priority": "P2",
        "created": "2026-06-29T10:00:00+00:00",
        "assigned_to": assigned,
        "summary": f"Order stuck — {jira_id}",
        "background": "",
        "linked_issues": [],
    }


@pytest.fixture()
def tmp_store(tmp_path: Path) -> CaseStore:
    return CaseStore(db_path=tmp_path / "state.db")


def _make_poller(
    incidents: list[dict[str, Any]],
    *,
    store: CaseStore,
    factory: Any = None,
    prep: Any = None,
) -> JiraPoller:
    return JiraPoller(
        jira_client=StubJiraClient(incidents),
        state_store=store,
        background_prep=prep,
        orchestrator_factory=factory,
    )


# ── Tests: new unassigned incident ────────────────────────────────────────────


async def test_new_unassigned_incident_starts_background_prep(
    tmp_store: CaseStore,
) -> None:
    prep = AsyncMock()
    prep.prepare = AsyncMock(return_value=None)

    poller = _make_poller([_ticket(assigned=None)], store=tmp_store, prep=prep)
    await poller._poll_once()

    assert "WH-1" in poller._background_tasks
    assert "WH-1" not in poller._active_cases
    prep.prepare.assert_called_once()


async def test_new_unassigned_incident_is_saved_to_store(tmp_store: CaseStore) -> None:
    poller = _make_poller([_ticket(assigned=None)], store=tmp_store)
    await poller._poll_once()

    case = tmp_store.load_case_by_jira_id("WH-1")
    assert case is not None
    assert case.jira_ticket_id == "WH-1"
    assert case.client == "acme"


# ── Tests: newly assigned incident ───────────────────────────────────────────


async def test_newly_assigned_incident_spawns_orchestrator(tmp_store: CaseStore) -> None:
    spawned: list[str] = []

    async def factory(case):  # type: ignore[type-arg]
        spawned.append(case.jira_ticket_id)

    # First poll: unassigned — seen but not orchestrated
    stub_jira = StubJiraClient([_ticket(assigned=None)])
    poller = JiraPoller(stub_jira, tmp_store, orchestrator_factory=factory)
    await poller._poll_once()
    assert "WH-1" in poller._seen_jira_ids
    assert "WH-1" not in poller._active_cases

    # Second poll: now assigned
    stub_jira.update_incident("WH-1", assigned_to="eng@example.com")
    await poller._poll_once()
    await asyncio.sleep(0)  # let the spawned task run

    assert "WH-1" in poller._active_cases
    assert spawned == ["WH-1"]


async def test_already_assigned_on_first_poll_skips_prep(tmp_store: CaseStore) -> None:
    """Incident arrives already assigned — no background prep, straight to orchestrator."""
    spawned: list[str] = []

    async def factory(case):  # type: ignore[type-arg]
        spawned.append(case.jira_ticket_id)

    poller = _make_poller(
        [_ticket(assigned="eng@example.com")],
        store=tmp_store,
        factory=factory,
    )
    await poller._poll_once()
    await asyncio.sleep(0)  # let the spawned task run

    assert "WH-1" not in poller._background_tasks
    assert "WH-1" in poller._active_cases
    assert spawned == ["WH-1"]


async def test_orchestrator_case_has_triaging_status(tmp_store: CaseStore) -> None:
    async def factory(case):  # type: ignore[type-arg]
        pass

    poller = _make_poller(
        [_ticket(assigned="eng@example.com")],
        store=tmp_store,
        factory=factory,
    )
    await poller._poll_once()

    case = tmp_store.load_case_by_jira_id("WH-1")
    assert case is not None
    assert case.status == CaseStatus.triaging
    assert case.assignee_email == "eng@example.com"


# ── Tests: reassignment ───────────────────────────────────────────────────────


async def test_reassignment_updates_case_in_store(tmp_store: CaseStore) -> None:
    call_count = 0

    async def factory(case):  # type: ignore[type-arg]
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(10)  # keep running across polls

    stub_jira = StubJiraClient([_ticket(assigned="eng1@example.com")])
    poller = JiraPoller(stub_jira, tmp_store, orchestrator_factory=factory)

    # First poll: spawns orchestrator for eng1
    await poller._poll_once()
    await asyncio.sleep(0)  # let the factory task start
    assert call_count == 1

    # Second poll: reassigned to eng2
    stub_jira.update_incident("WH-1", assigned_to="eng2@example.com")
    await poller._poll_once()

    # Orchestrator NOT re-spawned (still running)
    assert call_count == 1
    # But store updated
    case = tmp_store.load_case_by_jira_id("WH-1")
    assert case is not None
    assert case.assignee_email == "eng2@example.com"


# ── Tests: orchestrator cap ───────────────────────────────────────────────────


async def test_orchestrator_cap_prevents_spawn(tmp_store: CaseStore) -> None:
    from support_orchestration.config.base import MAX_CONCURRENT_ORCHESTRATORS

    spawned: list[str] = []

    async def factory(case):  # type: ignore[type-arg]
        spawned.append(case.jira_ticket_id)
        await asyncio.sleep(10)  # keep running

    # Fill up to the cap
    incidents = [
        _ticket(f"WH-{i}", assigned="eng@example.com")
        for i in range(1, MAX_CONCURRENT_ORCHESTRATORS + 2)
    ]
    poller = _make_poller(incidents, store=tmp_store, factory=factory)
    await poller._poll_once()

    assert len(poller._active_cases) == MAX_CONCURRENT_ORCHESTRATORS
    # The last incident is not in active_cases (cap hit)
    overflow_id = f"WH-{MAX_CONCURRENT_ORCHESTRATORS + 1}"
    assert overflow_id not in poller._active_cases


# ── Tests: reaping ────────────────────────────────────────────────────────────


async def test_reap_completes_removes_from_active(tmp_store: CaseStore) -> None:
    async def factory(case):  # type: ignore[type-arg]
        return  # completes immediately

    poller = _make_poller(
        [_ticket(assigned="eng@example.com")],
        store=tmp_store,
        factory=factory,
    )
    await poller._poll_once()
    assert "WH-1" in poller._active_cases

    # Let the task finish
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Next poll reaps it
    await poller._poll_once()
    assert "WH-1" not in poller._active_cases


# ── Tests: idempotence ────────────────────────────────────────────────────────


async def test_second_poll_does_not_duplicate_tasks(tmp_store: CaseStore) -> None:
    prep = AsyncMock()
    prep.prepare = AsyncMock(return_value=None)

    poller = _make_poller([_ticket(assigned=None)], store=tmp_store, prep=prep)
    await poller._poll_once()
    await poller._poll_once()

    assert prep.prepare.call_count == 1
