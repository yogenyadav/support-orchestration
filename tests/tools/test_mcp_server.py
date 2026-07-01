import pytest
from mcp.server.fastmcp import FastMCP

from support_orchestration.tools.hooks import ALLOWED_TOOLS
from support_orchestration.tools.mcp_server import (
    StubDbAdapter,
    StubGithubAdapter,
    StubLogAdapter,
    StubPhoenixAdapter,
    StubVectorAdapter,
    build_mcp_server,
    make_agent_hooks,
)


def test_build_mcp_server_returns_fastmcp():
    mcp = build_mcp_server()
    assert isinstance(mcp, FastMCP)


def test_registered_tool_names_match_allowlist():
    mcp = build_mcp_server()
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {"db_state_read", "log_read", "github_read", "history_search", "phoenix_resolve"}
    assert registered == expected


def test_allowlist_bare_names_match_mcp_prefix():
    """ALLOWED_TOOLS must be mcp__support__<bare_name> for each registered tool."""
    mcp = build_mcp_server()
    bare_names = {t.name for t in mcp._tool_manager.list_tools()}
    for bare in bare_names:
        assert f"mcp__support__{bare}" in ALLOWED_TOOLS, (
            f"mcp__support__{bare} missing from ALLOWED_TOOLS"
        )


def test_build_mcp_server_accepts_stub_adapters():
    mcp = build_mcp_server(
        db=StubDbAdapter(),
        log=StubLogAdapter(),
        vector=StubVectorAdapter(),
        phoenix=StubPhoenixAdapter(),
        github=StubGithubAdapter(),
    )
    assert isinstance(mcp, FastMCP)


def test_make_agent_hooks_structure():
    class _FakeCase:
        client = "acme"
        case_id = "case-xyz"

    hooks = make_agent_hooks(_FakeCase())
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    assert callable(hooks["PreToolUse"])
    assert callable(hooks["PostToolUse"])


def test_make_agent_hooks_pre_blocks_writes():
    class _FakeCase:
        client = "acme"
        case_id = "case-xyz"

    hooks = make_agent_hooks(_FakeCase())
    with pytest.raises(PermissionError, match="GUARDRAIL"):
        hooks["PreToolUse"]("mcp__support__db_state_write", {"client_id": "acme"})


def test_make_agent_hooks_post_calls_audit_store(tmp_path):
    from support_orchestration.storage.audit import AuditStore

    class _FakeCase:
        client = "acme"
        case_id = "case-audit"

    store = AuditStore(db_path=tmp_path / "audit.db")
    hooks = make_agent_hooks(_FakeCase(), audit_store=store)
    hooks["PostToolUse"](
        "mcp__support__db_state_read",
        {"client_id": "acme", "entity_type": "order", "entity_id": "ORD-1"},
        {"found": True},
    )
    rows = store.reads_for_case("case-audit")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "mcp__support__db_state_read"
