"""Tests for Jira write_resolution — the only write in the system."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from support_orchestration.glue.jira import (
    AtlassianJiraClient,
    JiraClient,
    StubJiraClient,
    write_resolution,
)
from support_orchestration.models.case import Case, Priority


def _make_case(**overrides: object) -> Case:
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = {
        "jira_ticket_id": "WH-1",
        "client": "acme",
        "priority": Priority.P2,
        "created_at": now,
        "sla_deadline": now + timedelta(hours=8),
    }
    defaults.update(overrides)
    return Case(**defaults)  # type: ignore[arg-type]


# ── StubJiraClient.write_resolution ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_write_resolution_captures(stub_jira: StubJiraClient):
    await stub_jira.write_resolution("WH-1", "Service down", "Restarted consumer")
    assert len(stub_jira.written_resolutions) == 1
    rec = stub_jira.written_resolutions[0]
    assert rec["ticket_id"] == "WH-1"
    assert rec["diagnosis_summary"] == "Service down"
    assert rec["fix_summary"] == "Restarted consumer"


@pytest.mark.asyncio
async def test_stub_write_resolution_accumulates(stub_jira: StubJiraClient):
    await stub_jira.write_resolution("WH-1", "d1", "f1")
    await stub_jira.write_resolution("WH-2", "d2", "f2")
    assert len(stub_jira.written_resolutions) == 2
    assert stub_jira.written_resolutions[1]["ticket_id"] == "WH-2"


@pytest.mark.asyncio
async def test_stub_write_resolution_async(stub_jira: StubJiraClient):
    await stub_jira.write_resolution("WH-99", "async test", "fixed async")
    assert stub_jira.written_resolutions[0]["ticket_id"] == "WH-99"


# ── module-level write_resolution ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_module_write_resolution_delegates(stub_jira: StubJiraClient):
    case = _make_case(jira_ticket_id="WH-42")
    await write_resolution(case, "Root cause X", "Applied fix Y", jira_client=stub_jira)
    assert stub_jira.written_resolutions[0]["ticket_id"] == "WH-42"
    assert stub_jira.written_resolutions[0]["diagnosis_summary"] == "Root cause X"
    assert stub_jira.written_resolutions[0]["fix_summary"] == "Applied fix Y"


# ── AtlassianJiraClient.write_resolution ─────────────────────────────────────

def _make_atlassian_client(jira_mock: object) -> AtlassianJiraClient:
    """Build an AtlassianJiraClient with a mocked _jira, bypassing real Atlassian auth."""
    from unittest.mock import patch
    with patch("atlassian.Jira", return_value=jira_mock):
        client = AtlassianJiraClient(
            base_url="https://test.atlassian.net",
            email="bot@test.com",
            api_token="token",
        )
    client._jira = jira_mock  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_atlassian_write_resolution_adds_comment():
    """write_resolution must add a Jira comment with both diagnosis and fix text."""
    from unittest.mock import MagicMock

    jira_mock = MagicMock()
    jira_mock.get_issue_transitions.return_value = [
        {"id": 31, "name": "Resolve Issue", "to": "Resolved"}
    ]
    client = _make_atlassian_client(jira_mock)

    await client.write_resolution("WH-5", "root cause text", "fix applied text")

    jira_mock.issue_add_comment.assert_called_once()
    call_args = jira_mock.issue_add_comment.call_args
    assert "WH-5" in str(call_args)
    comment_body = call_args[0][1]
    assert "root cause text" in comment_body
    assert "fix applied text" in comment_body


@pytest.mark.asyncio
async def test_atlassian_write_resolution_transitions_ticket():
    """write_resolution must attempt to transition the ticket to Resolved."""
    from unittest.mock import MagicMock

    jira_mock = MagicMock()
    jira_mock.get_issue_transitions.return_value = [
        {"id": 11, "name": "In Progress", "to": "In Progress"},
        {"id": 31, "name": "Resolve Issue", "to": "Resolved"},
    ]
    client = _make_atlassian_client(jira_mock)

    await client.write_resolution("WH-5", "diag", "fix")

    jira_mock.set_issue_status_by_transition_id.assert_called_once_with("WH-5", 31)


@pytest.mark.asyncio
async def test_atlassian_write_resolution_continues_on_transition_failure():
    """If transition fails, the comment write still succeeded — no exception raised."""
    from unittest.mock import MagicMock

    jira_mock = MagicMock()
    jira_mock.get_issue_transitions.side_effect = RuntimeError("Jira API down")
    client = _make_atlassian_client(jira_mock)

    # Should NOT raise — comment is written, transition failure is logged
    await client.write_resolution("WH-5", "diag", "fix")
    jira_mock.issue_add_comment.assert_called_once()


@pytest.mark.asyncio
async def test_atlassian_write_resolution_no_matching_transition():
    """If no transition name matches, log a warning but don't fail."""
    from unittest.mock import MagicMock

    jira_mock = MagicMock()
    jira_mock.get_issue_transitions.return_value = [
        {"id": 1, "name": "In Progress", "to": "In Progress"},
        {"id": 2, "name": "Backlog", "to": "Backlog"},
    ]
    client = _make_atlassian_client(jira_mock)

    await client.write_resolution("WH-5", "diag", "fix")

    jira_mock.set_issue_status_by_transition_id.assert_not_called()
    jira_mock.issue_add_comment.assert_called_once()


# ── JiraClient ABC ────────────────────────────────────────────────────────────

def test_stub_jira_is_jira_client():
    assert isinstance(StubJiraClient(), JiraClient)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def stub_jira() -> StubJiraClient:
    return StubJiraClient()
