"""Tests for PreToolUse / PostToolUse hooks — the structural guardrail layer."""

import pytest

from support_orchestration.tools.hooks import (
    ALLOWED_TOOLS,
    block_writes,
    enforce_allowlist,
    enforce_client_scope,
    enforce_turn_cap,
)


def test_block_writes_rejects_write_tools() -> None:
    with pytest.raises(PermissionError, match="write tool"):
        block_writes("mcp__support__db_write", {})


def test_block_writes_rejects_delete_tools() -> None:
    with pytest.raises(PermissionError):
        block_writes("mcp__support__order_delete", {})


def test_block_writes_allows_read_tools() -> None:
    block_writes("mcp__support__db_state_read", {})  # must not raise


def test_enforce_client_scope_passes_matching_client() -> None:
    enforce_client_scope("mcp__support__db_state_read", {"client_id": "acme"}, "acme")


def test_enforce_client_scope_raises_on_mismatch() -> None:
    with pytest.raises(PermissionError, match="cross-client"):
        enforce_client_scope("mcp__support__db_state_read", {"client_id": "acme"}, "other-client")


def test_enforce_client_scope_raises_when_missing() -> None:
    with pytest.raises(PermissionError, match="without client_id"):
        enforce_client_scope("mcp__support__db_state_read", {}, "acme")


def test_enforce_allowlist_passes_known_tool() -> None:
    enforce_allowlist("mcp__support__db_state_read", ALLOWED_TOOLS)


def test_enforce_allowlist_raises_unknown_tool() -> None:
    with pytest.raises(PermissionError, match="not on the allowlist"):
        enforce_allowlist("mcp__support__unknown_tool", ALLOWED_TOOLS)


def test_enforce_turn_cap_raises_at_limit() -> None:
    with pytest.raises(RuntimeError, match="turn cap"):
        enforce_turn_cap(current_turn=12, max_turns=12)


def test_enforce_turn_cap_passes_under_limit() -> None:
    enforce_turn_cap(current_turn=5, max_turns=12)  # must not raise
