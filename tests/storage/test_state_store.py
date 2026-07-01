"""Tests for CaseStore — SQLite-backed Case persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from support_orchestration.models import Case, CaseStatus, ConnectivityTier, Priority
from support_orchestration.storage.state_store import CaseStore


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_case(
    jira_id: str = "WH-1",
    client: str = "acme",
    priority: Priority = Priority.P2,
) -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        jira_ticket_id=jira_id,
        client=client,
        priority=priority,
        created_at=now,
        sla_deadline=now + timedelta(hours=8),
    )


@pytest.fixture()
def store(tmp_path: Path) -> CaseStore:
    return CaseStore(db_path=tmp_path / "state.db")


# ── Tests: basic CRUD ─────────────────────────────────────────────────────────


def test_save_and_load_by_case_id(store: CaseStore) -> None:
    case = _make_case()
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert loaded.case_id == case.case_id
    assert loaded.jira_ticket_id == "WH-1"
    assert loaded.client == "acme"
    assert loaded.priority == Priority.P2


def test_save_and_load_by_jira_id(store: CaseStore) -> None:
    case = _make_case(jira_id="WH-42")
    store.save_case(case)

    loaded = store.load_case_by_jira_id("WH-42")
    assert loaded is not None
    assert loaded.jira_ticket_id == "WH-42"


def test_load_missing_case_returns_none(store: CaseStore) -> None:
    assert store.load_case("nonexistent-id") is None


def test_load_missing_jira_id_returns_none(store: CaseStore) -> None:
    assert store.load_case_by_jira_id("WH-9999") is None


# ── Tests: update (upsert) ────────────────────────────────────────────────────


def test_save_updates_existing_case(store: CaseStore) -> None:
    case = _make_case()
    store.save_case(case)

    case.status = CaseStatus.triaging
    case.assignee_email = "eng@example.com"
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert loaded.status == CaseStatus.triaging
    assert loaded.assignee_email == "eng@example.com"


# ── Tests: round-trip fidelity ────────────────────────────────────────────────


def test_datetime_round_trip(store: CaseStore) -> None:
    case = _make_case()
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert abs((loaded.created_at - case.created_at).total_seconds()) < 1


def test_reroute_guard_set_round_trip(store: CaseStore) -> None:
    case = _make_case()
    case.reroute_guard = {"WES", "IMS"}
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert loaded.reroute_guard == {"WES", "IMS"}


def test_connectivity_tier_round_trip(store: CaseStore) -> None:
    case = _make_case()
    case.connectivity_tier = ConnectivityTier.direct_connect
    case.log_posture = "direct"
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert loaded.connectivity_tier == ConnectivityTier.direct_connect
    assert loaded.log_posture == "direct"


def test_evidence_list_round_trip(store: CaseStore) -> None:
    case = _make_case()
    case.add_evidence(source="db:orders", entity_id="12345", summary="state=prioritized")
    store.save_case(case)

    loaded = store.load_case(case.case_id)
    assert loaded is not None
    assert len(loaded.evidence) == 1
    assert loaded.evidence[0].source == "db:orders"
    assert loaded.evidence[0].entity_id == "12345"


# ── Tests: active IDs query ───────────────────────────────────────────────────


def test_get_active_jira_ids(store: CaseStore) -> None:
    c1 = _make_case(jira_id="WH-1")
    c2 = _make_case(jira_id="WH-2")
    c3 = _make_case(jira_id="WH-3")
    c3.status = CaseStatus.resolved

    store.save_case(c1)
    store.save_case(c2)
    store.save_case(c3)

    active = store.get_active_jira_ids()
    assert "WH-1" in active
    assert "WH-2" in active
    assert "WH-3" not in active  # resolved


def test_closed_case_excluded_from_active_ids(store: CaseStore) -> None:
    c = _make_case(jira_id="WH-99")
    c.status = CaseStatus.closed
    store.save_case(c)

    assert "WH-99" not in store.get_active_jira_ids()


# ── Tests: multi-client isolation ────────────────────────────────────────────


def test_cases_from_different_clients_are_independent(store: CaseStore) -> None:
    c1 = _make_case(jira_id="WH-10", client="acme")
    c2 = _make_case(jira_id="WH-11", client="globex")
    store.save_case(c1)
    store.save_case(c2)

    loaded_acme = store.load_case_by_jira_id("WH-10")
    loaded_globex = store.load_case_by_jira_id("WH-11")
    assert loaded_acme is not None and loaded_acme.client == "acme"
    assert loaded_globex is not None and loaded_globex.client == "globex"
