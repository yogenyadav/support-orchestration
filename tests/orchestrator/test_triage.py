"""Tests for triage helpers — lifecycle map loading and transition lookup."""

import pytest

from support_orchestration.orchestrator.triage import find_transition, load_lifecycle_map


def test_load_order_map() -> None:
    m = load_lifecycle_map("order")
    assert m["entity"] == "order"
    assert "transitions" in m
    assert len(m["transitions"]) > 0


def test_load_tote_map() -> None:
    m = load_lifecycle_map("tote")
    assert m["entity"] == "tote"


def test_find_transition_returns_correct_entry() -> None:
    m = load_lifecycle_map("order")
    t = find_transition(m, "prioritized")
    assert t is not None
    assert t["to"] == "released"
    assert t["owning_domain"] == "WES"


def test_find_transition_returns_none_for_terminal() -> None:
    m = load_lifecycle_map("order")
    t = find_transition(m, "shipped")
    assert t is None


def test_load_unknown_entity_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_lifecycle_map("unknown_entity")
