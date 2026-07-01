"""Agent SDK hooks — structural guardrails that run regardless of model output.

docs/3-agent-design.md §3.6 + docs/4-technical-build.md §4.10.

PreToolUse hooks:  block_writes | enforce_client_scope | enforce_allowlist | enforce_turn_cap
PostToolUse hook:  audit_read

These are the load-bearing safety layer. Prompts guide; hooks enforce.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Tools that the agent is allowed to call. Populated per-agent at options build time.
# Stored here as the canonical source; agents import ALLOWED_TOOLS and pass to options.
ALLOWED_TOOLS: frozenset[str] = frozenset({
    "mcp__support__db_state_read",
    "mcp__support__log_read",
    "mcp__support__github_read",
    "mcp__support__history_search",
    "mcp__support__phoenix_resolve",
})

# Any tool name containing these substrings is a write and must never be called.
_WRITE_INDICATORS = ("write", "update", "delete", "insert", "create", "mutate", "patch", "post")


def block_writes(tool_name: str, tool_input: dict) -> None:  # type: ignore[type-arg]
    """PreToolUse: raise immediately if the tool name suggests a write operation."""
    lower = tool_name.lower()
    if any(w in lower for w in _WRITE_INDICATORS):
        raise PermissionError(
            f"GUARDRAIL: write tool '{tool_name}' is not permitted. "
            "No write tool exists in this system. Agents read, reason, and recommend; humans act."
        )


def enforce_client_scope(tool_name: str, tool_input: dict, case_client: str) -> None:  # type: ignore[type-arg]
    """PreToolUse: ensure every tool call is scoped to the current case's client."""
    call_client = tool_input.get("client_id") or tool_input.get("client")
    if call_client is None:
        raise PermissionError(
            f"GUARDRAIL: tool '{tool_name}' called without client_id. "
            "Every tool call must be scoped to case.client."
        )
    if call_client != case_client:
        raise PermissionError(
            f"GUARDRAIL: cross-client access attempt — tool '{tool_name}' called with "
            f"client_id='{call_client}' but case is scoped to '{case_client}'."
        )


def enforce_allowlist(tool_name: str, allowed_tools: frozenset[str]) -> None:
    """PreToolUse: reject any tool not on the explicit per-agent allowlist."""
    if tool_name not in allowed_tools:
        raise PermissionError(
            f"GUARDRAIL: tool '{tool_name}' is not on the allowlist for this agent. "
            f"Allowed: {sorted(allowed_tools)}"
        )


def enforce_turn_cap(current_turn: int, max_turns: int) -> None:
    """PreToolUse: hard-stop the agent if it has exceeded max_turns."""
    if current_turn >= max_turns:
        raise RuntimeError(
            f"GUARDRAIL: turn cap reached ({current_turn}/{max_turns}). "
            "Agent must terminate with its best partial diagnosis."
        )


def audit_read(
    tool_name: str,
    tool_input: dict,  # type: ignore[type-arg]
    tool_output: object,
    case_id: str,
    case_client: str,
    *,
    audit_store: object = None,
) -> None:
    """PostToolUse: log every read to the audit store (what / when / client / credential).

    Non-negotiable across 125 clients even for read-only access.
    Pass an AuditStore instance via audit_store for durable writes; omit for log-only
    (used by existing tests that pre-date the store).
    """
    input_keys = list(tool_input.keys())
    output_size = len(str(tool_output))
    if audit_store is not None:
        audit_store.append_read(  # type: ignore[union-attr]
            case_id=case_id,
            client=case_client,
            tool_name=tool_name,
            input_keys=input_keys,
            output_size=output_size,
        )
    logger.info(
        "AUDIT_READ tool=%s case=%s client=%s input_keys=%s output_size=%d",
        tool_name, case_id, case_client, input_keys, output_size,
    )
