"""Tests for intake.py — case_from_jira field mapping and SLA computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from support_orchestration.models import CaseStatus, Priority
from support_orchestration.watcher.intake import _parse_jira_datetime, case_from_jira

# ── _parse_jira_datetime ──────────────────────────────────────────────────────

def test_parse_jira_datetime_with_z() -> None:
    dt = _parse_jira_datetime("2026-06-29T10:00:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026
    assert dt.hour == 10


def test_parse_jira_datetime_with_offset_no_colon() -> None:
    dt = _parse_jira_datetime("2026-06-29T10:00:00.000+0000")
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_jira_datetime_with_colon_offset() -> None:
    dt = _parse_jira_datetime("2026-06-29T10:00:00+00:00")
    assert dt == datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)


# ── case_from_jira ────────────────────────────────────────────────────────────

def _make_ticket(**overrides: object) -> dict:
    base = {
        "id": "WH-1234",
        "client": "acme",
        "priority": "P2",
        "created": "2026-06-29T08:00:00.000+0000",
        "assigned_to": "eng@example.com",
        "summary": "Order 12345 stuck in prioritized",
        "background": "WES release not firing.",
        "linked_issues": ["WH-900"],
    }
    base.update(overrides)
    return base


def test_basic_field_mapping() -> None:
    case = case_from_jira(_make_ticket())
    assert case.jira_ticket_id == "WH-1234"
    assert case.client == "acme"
    assert case.priority == Priority.P2
    assert case.assignee_email == "eng@example.com"


def test_description_concatenates_summary_and_background() -> None:
    case = case_from_jira(_make_ticket())
    assert "Order 12345 stuck in prioritized" in case.description
    assert "WES release not firing" in case.description


def test_empty_background_does_not_add_newline() -> None:
    case = case_from_jira(_make_ticket(background=""))
    assert case.description == "Order 12345 stuck in prioritized"


def test_unassigned_ticket_has_none_assignee() -> None:
    case = case_from_jira(_make_ticket(assigned_to=None))
    assert case.assignee_email is None


def test_initial_status_is_prepping() -> None:
    case = case_from_jira(_make_ticket())
    assert case.status == CaseStatus.prepping


def test_case_id_is_generated_uuid() -> None:
    import re
    case = case_from_jira(_make_ticket())
    assert re.match(r"[0-9a-f-]{36}", case.case_id)


def test_created_at_is_timezone_aware() -> None:
    case = case_from_jira(_make_ticket())
    assert case.created_at.tzinfo is not None


@pytest.mark.parametrize("priority,expected_hours", [
    ("P1", 4),
    ("P2", 8),
    ("P3", 72),
    ("P4", 168),
])
def test_sla_deadline_from_priority(priority: str, expected_hours: int) -> None:
    case = case_from_jira(_make_ticket(priority=priority))
    delta = case.sla_deadline - case.created_at
    assert delta == timedelta(hours=expected_hours)


def test_missing_created_falls_back_to_now() -> None:
    case = case_from_jira(_make_ticket(created=""))
    now = datetime.now(timezone.utc)
    assert abs((case.created_at - now).total_seconds()) < 5


def test_client_falls_back_to_ticket_id_prefix() -> None:
    ticket = _make_ticket()
    del ticket["client"]
    case = case_from_jira(ticket)
    assert case.client == "wh"
