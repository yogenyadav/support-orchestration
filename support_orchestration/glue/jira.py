"""Jira integration — reads all tickets; writes ONLY the resolution record.

The only write in the system is the resolution back to Jira (the support system
of record, not a client production system). All reads go through the Atlassian
MCP server or the JiraClient interface below.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from support_orchestration.models import Case

logger = logging.getLogger(__name__)

_RESOLVE_TRANSITION_NAMES = frozenset({
    "resolve issue", "resolve", "done", "close issue", "close",
})

# ── Priority normalisation ────────────────────────────────────────────────────

_PRIORITY_MAP: dict[str, str] = {
    "highest": "P1",
    "critical": "P1",
    "high": "P2",
    "medium": "P3",
    "low": "P4",
    "lowest": "P4",
}


def _normalize_priority(raw: str) -> str:
    upper = raw.strip().upper()
    if upper in ("P1", "P2", "P3", "P4"):
        return upper
    return _PRIORITY_MAP.get(raw.strip().lower(), "P3")


# ── Client interface ──────────────────────────────────────────────────────────

class JiraClient(ABC):
    """Jira client interface. Reads are universal; the one allowed write is write_resolution."""

    @abstractmethod
    async def fetch_open_incidents(self) -> list[dict[str, Any]]:
        """Return all open (non-resolved) incidents as normalized ticket dicts."""
        ...

    @abstractmethod
    async def read_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Read one ticket by Jira key. Returns normalized ticket dict."""
        ...

    @abstractmethod
    async def write_resolution(
        self,
        ticket_id: str,
        diagnosis_summary: str,
        fix_summary: str,
    ) -> None:
        """
        Write root cause + fix to Jira. This is the ONLY write in the entire system.

        Jira is the support system of record (not a client production system), so
        writing the resolution is permitted and required — it enriches history so
        the next incident with the same pattern gets a faster answer via history_search.

        Implementations MUST:
          1. Add a comment with diagnosis + fix summary.
          2. Attempt to transition the ticket to Resolved (best-effort; log and continue if
             the transition name differs from the configured set).
        """
        ...


# ── Stub client (tests and local dev) ────────────────────────────────────────

class StubJiraClient(JiraClient):
    """In-memory stub. Pre-load incidents; mutate them to simulate Jira state changes."""

    def __init__(self, incidents: list[dict[str, Any]] | None = None) -> None:
        self._incidents: list[dict[str, Any]] = list(incidents or [])
        self.written_resolutions: list[dict[str, Any]] = []

    async def fetch_open_incidents(self) -> list[dict[str, Any]]:
        return [dict(t) for t in self._incidents]

    async def read_ticket(self, ticket_id: str) -> dict[str, Any]:
        for t in self._incidents:
            if t["id"] == ticket_id:
                return dict(t)
        raise KeyError(f"Ticket {ticket_id!r} not found in stub")

    async def write_resolution(
        self,
        ticket_id: str,
        diagnosis_summary: str,
        fix_summary: str,
    ) -> None:
        self.written_resolutions.append({
            "ticket_id": ticket_id,
            "diagnosis_summary": diagnosis_summary,
            "fix_summary": fix_summary,
        })
        logger.info("STUB_JIRA_WRITE resolution for %s", ticket_id)

    def add_incident(self, ticket: dict[str, Any]) -> None:
        self._incidents.append(ticket)

    def update_incident(self, ticket_id: str, **updates: Any) -> None:
        for t in self._incidents:
            if t["id"] == ticket_id:
                t.update(updates)
                return
        raise KeyError(f"Ticket {ticket_id!r} not found in stub")

    def remove_incident(self, ticket_id: str) -> None:
        self._incidents = [t for t in self._incidents if t["id"] != ticket_id]


# ── Real Atlassian client ─────────────────────────────────────────────────────

class AtlassianJiraClient(JiraClient):
    """
    Thin read-only wrapper over atlassian-python-api.

    Runs sync Atlassian SDK calls in an executor thread so the event loop
    stays unblocked.

    Args:
        base_url:        Atlassian instance URL (e.g. https://example.atlassian.net).
        email:           Service-account email for API auth.
        api_token:       Atlassian API token (read-only scoped recommended).
        project_key:     Jira project key to poll (e.g. "WH").
        client_field:    Jira custom field name that stores the client identifier.
        background_field: Field name used for ticket "background" (default: "description").
    """

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str = "WH",
        client_field: str = "customfield_10001",
        background_field: str = "description",
    ) -> None:
        from atlassian import Jira

        self._jira = Jira(url=base_url, username=email, password=api_token, cloud=True)
        self._project_key = project_key
        self._client_field = client_field
        self._background_field = background_field

    async def fetch_open_incidents(self) -> list[dict[str, Any]]:
        def _sync() -> list[dict[str, Any]]:
            jql = (
                f"project = {self._project_key} "
                "AND status NOT IN (Resolved, Closed, Done) "
                "ORDER BY created ASC"
            )
            result: dict[str, Any] = self._jira.jql(
                jql,
                limit=50,
                fields=[
                    "summary",
                    "description",
                    "priority",
                    "status",
                    "assignee",
                    "created",
                    "issuelinks",
                    self._client_field,
                ],
            ) or {}
            return [self._normalize(t) for t in result.get("issues", [])]

        return await asyncio.to_thread(_sync)

    async def read_ticket(self, ticket_id: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            raw: dict[str, Any] = self._jira.issue(ticket_id)
            return self._normalize(raw)

        return await asyncio.to_thread(_sync)

    async def write_resolution(
        self,
        ticket_id: str,
        diagnosis_summary: str,
        fix_summary: str,
    ) -> None:
        comment = (
            f"*Agent Diagnosis:*\n{diagnosis_summary}\n\n"
            f"*Applied Fix (by engineer):*\n{fix_summary}"
        )

        def _sync() -> None:
            self._jira.issue_add_comment(ticket_id, comment)
            # Attempt resolution transition — name varies by Jira workflow config.
            # Log and continue if not found rather than failing the write.
            try:
                transitions: list[dict[str, Any]] = (
                    self._jira.get_issue_transitions(ticket_id) or []
                )
                for t in transitions:
                    if str(t.get("name", "")).lower() in _RESOLVE_TRANSITION_NAMES:
                        self._jira.set_issue_status_by_transition_id(ticket_id, t["id"])
                        logger.info("JIRA_TRANSITION %s → %s", ticket_id, t["name"])
                        break
                else:
                    logger.warning(
                        "JIRA_NO_RESOLVE_TRANSITION for %s — comment written but "
                        "ticket not auto-transitioned. Transition manually if needed.",
                        ticket_id,
                    )
            except Exception as exc:
                logger.warning(
                    "JIRA_TRANSITION_FAILED for %s (%s) — comment written.", ticket_id, exc,
                )

        await asyncio.to_thread(_sync)
        logger.info("JIRA_RESOLUTION_WRITTEN ticket=%s", ticket_id)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize Atlassian API response to internal ticket dict."""
        fields: dict[str, Any] = raw.get("fields", {})

        priority = _normalize_priority(
            (fields.get("priority") or {}).get("name", "medium")
        )

        assignee: str | None = None
        if a := fields.get("assignee"):
            assignee = a.get("emailAddress")

        linked: list[str] = []
        for link in fields.get("issuelinks", []):
            key = (
                (link.get("outwardIssue") or link.get("inwardIssue") or {}).get("key")
            )
            if key:
                linked.append(key)

        # Client: custom field → fall back to project prefix (e.g. "WH" → "wh")
        client_raw = fields.get(self._client_field)
        client = str(client_raw) if client_raw else raw.get("key", "WH-0").split("-")[0].lower()

        summary: str = fields.get("summary", "") or ""
        background: str = fields.get(self._background_field, "") or ""

        return {
            "id": raw.get("key", raw.get("id", "")),
            "client": client,
            "priority": priority,
            "created": fields.get("created", ""),
            "assigned_to": assignee,
            "summary": summary,
            "background": background,
            "linked_issues": linked,
        }


# ── Module-level helpers ──────────────────────────────────────────────────────

async def read_ticket(ticket_id: str, client: JiraClient | None = None) -> dict[str, Any]:
    """Read a Jira ticket using the provided client."""
    if client is None:
        raise RuntimeError(
            "No JiraClient provided. Pass a StubJiraClient for tests or an "
            "AtlassianJiraClient configured from environment variables."
        )
    return await client.read_ticket(ticket_id)


async def write_resolution(
    case: Case,
    diagnosis_summary: str,
    fix_summary: str,
    *,
    jira_client: JiraClient,
) -> None:
    """
    Write root cause + resolution to the Jira ticket.

    This is the ONLY write in the entire system. Jira is the support system of
    record, not a client production system, so writing here is permitted.
    Triggers history enrichment — the next incident with the same pattern will
    find this record via history_search.

    Args:
        case:               The resolved incident's Case object.
        diagnosis_summary:  Root-cause text for the Jira comment.
        fix_summary:        Human-applied fix description for the Jira comment.
        jira_client:        Injected JiraClient (AtlassianJiraClient in prod, Stub in tests).
    """
    await jira_client.write_resolution(case.jira_ticket_id, diagnosis_summary, fix_summary)
    logger.info(
        "JIRA_RESOLUTION case=%s client=%s ticket=%s",
        case.case_id,
        case.client,
        case.jira_ticket_id,
    )
