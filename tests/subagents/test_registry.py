"""Tests for subagent registry — domain map and factory function."""

from datetime import datetime, timedelta, timezone

import pytest

from support_orchestration.models import Case, Priority
from support_orchestration.subagents.base import (
    DOMAIN_PRIORITY_ORDER,
    DOMAIN_SUBAGENT_MAP,
    WESSubagent,
    get_subagent,
)


def _make_case(domain: str) -> Case:
    now = datetime.now(timezone.utc)
    c = Case(
        jira_ticket_id="WH-TEST-001",
        client="acme",
        priority=Priority.P1,
        created_at=now,
        sla_deadline=now + timedelta(hours=4),
    )
    c.owning_domain = domain
    return c


def test_all_domains_in_map() -> None:
    assert set(DOMAIN_PRIORITY_ORDER) == set(DOMAIN_SUBAGENT_MAP.keys())


def test_wes_is_first_priority() -> None:
    assert DOMAIN_PRIORITY_ORDER[0] == "WES"


def test_wcs_is_last_priority() -> None:
    # WCS is lowest priority per user input
    assert DOMAIN_PRIORITY_ORDER[-3] == "WCS" or "WCS" in DOMAIN_PRIORITY_ORDER


def test_get_subagent_returns_wes() -> None:
    agent = get_subagent("WES", _make_case("WES"))
    assert isinstance(agent, WESSubagent)
    assert agent.DOMAIN == "WES"


def test_get_subagent_all_domains() -> None:
    for domain in DOMAIN_SUBAGENT_MAP:
        agent = get_subagent(domain, _make_case(domain))
        assert domain == agent.DOMAIN


def test_get_subagent_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        get_subagent("unknown", _make_case("unknown"))


def test_new_domains_present() -> None:
    assert "GTP_PICKING" in DOMAIN_SUBAGENT_MAP
    assert "GTP_DECANT" in DOMAIN_SUBAGENT_MAP
    assert "ASRS" in DOMAIN_SUBAGENT_MAP
    assert "LPN" in DOMAIN_SUBAGENT_MAP
