"""Intake — convert a raw Jira ticket dict into a Case object.

The ticket dict is produced by JiraClient.fetch_open_incidents() /
JiraClient.read_ticket(), which normalizes the Atlassian API response.
SLA deadline is computed from priority + created_at here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from support_orchestration.models import Case, Priority

_SLA_HOURS: dict[Priority, int] = {
    Priority.P1: 4,
    Priority.P2: 8,
    Priority.P3: 72,
    Priority.P4: 168,
}

# Atlassian datetime format variants
_TZ_VARIANTS = re.compile(r"([+-]\d{2})(\d{2})$")


def _parse_jira_datetime(dt_str: str) -> datetime:
    """Parse Jira ISO-8601 datetime strings to an aware UTC datetime.

    Handles formats like:
      2026-06-29T10:00:00.000+0000
      2026-06-29T10:00:00.000+00:00
      2026-06-29T10:00:00Z
    """
    s = dt_str.strip()
    # Normalize Z → +00:00
    s = s.replace("Z", "+00:00")
    # Normalize +0000 → +00:00 (no colon)
    s = _TZ_VARIANTS.sub(r"\1:\2", s)
    # Drop milliseconds: 2026-06-29T10:00:00.123+00:00 → 2026-06-29T10:00:00+00:00
    if "." in s:
        dot = s.index(".")
        plus = s.rfind("+", dot)
        minus = s.rfind("-", dot)
        sep = max(plus, minus)
        if sep > dot:
            s = s[:dot] + s[sep:]
    dt = datetime.fromisoformat(s)
    return dt.astimezone(timezone.utc)


def case_from_jira(ticket: dict[str, Any]) -> Case:
    """
    Build an initial Case from a normalized Jira ticket dict.

    Ticket keys (produced by JiraClient._normalize):
        id, client, priority, created, assigned_to,
        summary, background, linked_issues
    """
    priority = Priority(ticket["priority"])

    created_str: str = ticket.get("created", "")
    created_at = (
        _parse_jira_datetime(created_str) if created_str else datetime.now(timezone.utc)
    )

    sla_deadline = created_at + timedelta(hours=_SLA_HOURS[priority])

    summary: str = ticket.get("summary", "") or ""
    background: str = ticket.get("background", "") or ""
    description = "\n".join(filter(None, [summary, background]))

    return Case(
        jira_ticket_id=ticket["id"],
        client=ticket.get("client", ticket["id"].split("-")[0].lower()),
        assignee_email=ticket.get("assigned_to"),
        priority=priority,
        created_at=created_at,
        sla_deadline=sla_deadline,
        description=description,
    )
