"""Tests for the service composition root (support_orchestration/runtime.py).

Everything runs in mock mode — no external connectivity anywhere. The
end-to-end test drives one poll cycle of the real service wiring: stub Jira →
poller → case → orchestrator → stub dialect → state store.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from support_orchestration.glue.jira import AtlassianJiraClient, StubJiraClient
from support_orchestration.models import Case, Priority
from support_orchestration.models.case import CaseStatus
from support_orchestration.runtime import DEMO_TICKET, Runtime, load_runtime_config
from support_orchestration.tools.mcp_server import (
    StubDbAdapter,
    StubGithubAdapter,
    StubLogAdapter,
    StubPhoenixAdapter,
    StubVectorAdapter,
)

_PROD_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "ATLASSIAN_BASE_URL", "ATLASSIAN_USER", "ATLASSIAN_API_TOKEN",
    "TEAMS_APP_ID", "TEAMS_APP_PASSWORD", "TEAMS_TENANT_ID",
    "GITHUB_TOKEN", "PHOENIX_BASE_URL", "PHOENIX_API_TOKEN", "VECTOR_STORE_DSN",
]


def _config(tmp_path: Path, mode: str = "mock") -> dict[str, Any]:
    return {
        "mode": mode,
        "stores": {
            "state_db": str(tmp_path / "state.db"),
            "audit_db": str(tmp_path / "audit.db"),
        },
        "llm": {"api_key_env": "ANTHROPIC_API_KEY"},
        "jira": {
            "base_url_env": "ATLASSIAN_BASE_URL",
            "email_env": "ATLASSIAN_USER",
            "api_token_env": "ATLASSIAN_API_TOKEN",
            "project_key": "WH",
        },
        "teams": {
            "app_id_env": "TEAMS_APP_ID",
            "app_password_env": "TEAMS_APP_PASSWORD",
            "tenant_id_env": "TEAMS_TENANT_ID",
        },
    }


def _make_case(jira_id: str = "WH-77", assignee: str | None = "eng@acme.example") -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        jira_ticket_id=jira_id,
        client="acme",
        priority=Priority.P2,
        created_at=now,
        sla_deadline=now + timedelta(hours=8),
        assignee_email=assignee,
        status=CaseStatus.diagnosing,
        entity_type="order",
        entity_id="12345",
        entity_current_state="prioritized",
    )


# ── Config loading ─────────────────────────────────────────────────────────────

def test_load_runtime_config_missing_file_defaults_to_mock(tmp_path: Path) -> None:
    config = load_runtime_config(tmp_path / "nope.yaml")
    assert config == {"mode": "mock"}


def test_load_runtime_config_reads_repo_default() -> None:
    config = load_runtime_config()
    assert config["mode"] in ("mock", "production")
    assert "stores" in config


def test_invalid_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown mode"):
        Runtime(_config(tmp_path, mode="staging"))


# ── Mock-mode wiring ───────────────────────────────────────────────────────────

def test_mock_runtime_uses_stubs_everywhere(tmp_path: Path) -> None:
    rt = Runtime(_config(tmp_path))
    assert isinstance(rt.jira_client, StubJiraClient)
    assert rt.anthropic_client is None
    assert rt.teams_transport is None
    assert isinstance(rt.adapters["db"], StubDbAdapter)
    assert isinstance(rt.adapters["log"], StubLogAdapter)
    assert isinstance(rt.adapters["vector"], StubVectorAdapter)
    assert isinstance(rt.adapters["github"], StubGithubAdapter)
    assert isinstance(rt.adapters["phoenix"], StubPhoenixAdapter)
    assert (tmp_path / "state.db").exists()
    assert (tmp_path / "audit.db").exists()


def test_demo_seeds_incident(tmp_path: Path) -> None:
    rt = Runtime(_config(tmp_path), demo=True)
    assert isinstance(rt.jira_client, StubJiraClient)
    incidents = asyncio.run(rt.jira_client.fetch_open_incidents())
    assert [t["id"] for t in incidents] == [DEMO_TICKET["id"]]


# ── Production-mode fallbacks (no env → stubs, service still boots) ────────────

def test_production_mode_without_env_falls_back_to_stubs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in _PROD_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    rt = Runtime(_config(tmp_path, mode="production"))
    assert isinstance(rt.jira_client, StubJiraClient)
    assert rt.anthropic_client is None
    assert rt.teams_transport is None
    assert isinstance(rt.adapters["vector"], StubVectorAdapter)


def test_production_mode_with_jira_env_builds_atlassian_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in _PROD_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ATLASSIAN_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_USER", "bot@test.com")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "token")
    rt = Runtime(_config(tmp_path, mode="production"))
    assert isinstance(rt.jira_client, AtlassianJiraClient)


# ── Crash recovery ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rehydrate_resumes_assigned_case(tmp_path: Path) -> None:
    rt = Runtime(_config(tmp_path))
    case = _make_case()
    rt.state_store.save_case(case)

    resumed = rt.rehydrate()
    assert resumed == 1
    assert rt.poller.active_count == 1

    # Let the orchestrator task run to completion (mock mode, no LLM: it
    # triages deterministically and ends in a terminal state without raising).
    await asyncio.gather(*rt.poller._active_cases.values())
    reloaded = rt.state_store.load_case_by_jira_id("WH-77")
    assert reloaded is not None
    assert reloaded.status in (CaseStatus.escalated, CaseStatus.resolved)


@pytest.mark.asyncio
async def test_rehydrate_skips_unassigned_cases(tmp_path: Path) -> None:
    rt = Runtime(_config(tmp_path))
    rt.state_store.save_case(_make_case(jira_id="WH-88", assignee=None))
    assert rt.rehydrate() == 0
    assert rt.poller.active_count == 0


# ── End-to-end mocked service cycle ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_demo_poll_cycle_runs_orchestrator_end_to_end(tmp_path: Path) -> None:
    """One real poll of the fully wired mock service: stub Jira → poller →
    intake → orchestrator → stub dialect → state store. No external systems."""
    rt = Runtime(_config(tmp_path), demo=True)

    await rt.poller._poll_once()
    assert rt.poller.active_count == 1

    await asyncio.gather(*rt.poller._active_cases.values())

    case = rt.state_store.load_case_by_jira_id(DEMO_TICKET["id"])
    assert case is not None
    assert case.assignee_email == DEMO_TICKET["assigned_to"]
    # Without an LLM the orchestrator ends in a terminal, persisted state —
    # never hangs, never leaves the case mid-flight.
    assert case.status in (CaseStatus.escalated, CaseStatus.resolved)
    assert any(t.action == "dossier_sent" for t in case.trail)
