"""In-process MCP server registration.

Wires the custom read-only tools into a single FastMCP server that agents can call.
External connections (DB, S3, Phoenix, vector store, GitHub) are behind adapter interfaces
so tests can swap in recorded fixtures without touching live systems.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# ── Adapter interfaces ────────────────────────────────────────────────────────

class DbAdapter(ABC):
    @abstractmethod
    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        """Discover schema at runtime — never pre-loaded, always discovered per §4.11."""
        ...


class LogAdapter(ABC):
    @abstractmethod
    async def read_direct(self, client_id: str, host: str, query: str) -> str:
        ...

    @abstractmethod
    async def read_s3(self, client_id: str, bucket: str, prefix: str, query: str) -> str:
        ...

    def read_human_relay(self, client_id: str, question: str) -> dict[str, Any]:
        """Non-DC clients: signal the dialect layer to ask the engineer."""
        return {"relay_required": True, "client_id": client_id, "question": question}


class VectorStoreAdapter(ABC):
    @abstractmethod
    async def search(self, query: str, top_k: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def write(self, record: dict[str, Any]) -> None:
        """Upsert a resolved incident into the vector store.

        record keys: jira_id, client_id, entity_type, domain,
                     summary, root_cause, fix_summary
        """
        ...


class PhoenixAdapter(ABC):
    @abstractmethod
    async def resolve(self, client_id: str) -> dict[str, Any]:
        """Return connectivity tier + log posture for the client."""
        ...


class GithubAdapter(ABC):
    @abstractmethod
    def read_file(self, org: str, repo: str, path: str, ref: str = "main") -> str:
        """Read a file from GitHub. Returns raw file content."""
        ...


# ── Stub adapters (used in tests and local dev) ───────────────────────────────

class StubDbAdapter(DbAdapter):
    def __init__(self, fixture: list[dict[str, Any]] | None = None) -> None:
        self._fixture = fixture or []

    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._fixture

    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        return {}


class StubLogAdapter(LogAdapter):
    def __init__(self, fixture: str = "") -> None:
        self._fixture = fixture

    async def read_direct(self, client_id: str, host: str, query: str) -> str:
        return self._fixture

    async def read_s3(self, client_id: str, bucket: str, prefix: str, query: str) -> str:
        return self._fixture


class StubVectorAdapter(VectorStoreAdapter):
    def __init__(self, fixture: list[dict[str, Any]] | None = None) -> None:
        self._fixture = fixture or []
        self.written: list[dict[str, Any]] = []

    async def search(self, query: str, top_k: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return self._fixture

    async def write(self, record: dict[str, Any]) -> None:
        self.written.append(record)


class StubPhoenixAdapter(PhoenixAdapter):
    def __init__(self, tier: str = "human_relay", log_posture: str = "human_relay") -> None:
        self._tier = tier
        self._log_posture = log_posture

    async def resolve(self, client_id: str) -> dict[str, Any]:
        return {"connectivity_tier": self._tier, "log_posture": self._log_posture}


class StubGithubAdapter(GithubAdapter):
    def read_file(self, org: str, repo: str, path: str, ref: str = "main") -> str:
        return f"# stub content for {org}/{repo}/{path}@{ref}"


# ── Server factory ────────────────────────────────────────────────────────────

def build_mcp_server(
    db: DbAdapter | None = None,
    log: LogAdapter | None = None,
    vector: VectorStoreAdapter | None = None,
    phoenix: PhoenixAdapter | None = None,
    github: GithubAdapter | None = None,
) -> Any:
    """
    Register all read-only tools into a FastMCP in-process server.

    Returns a FastMCP instance to pass to the Agent SDK's mcp_servers option.
    Adapters default to stubs so tests need not pass real connections.
    """
    from mcp.server.fastmcp import FastMCP

    from support_orchestration.tools.db_state_reader import db_state_read as _db_fn
    from support_orchestration.tools.github_reader import github_read as _gh_fn
    from support_orchestration.tools.history_retrieval import history_search as _hist_fn
    from support_orchestration.tools.log_reader import log_read as _log_fn
    from support_orchestration.tools.phoenix_resolver import phoenix_resolve as _phx_fn

    _db_a = db or StubDbAdapter()
    _log_a = log or StubLogAdapter()
    _vec_a = vector or StubVectorAdapter()
    _phx_a = phoenix or StubPhoenixAdapter()
    _gh_a = github or StubGithubAdapter()

    mcp = FastMCP("support")  # server name "support" → tool names mcp__support__*

    @mcp.tool()
    async def db_state_read(
        client_id: str,
        entity_type: str,
        entity_id: str,
        table_hint: str | None = None,
    ) -> dict[str, Any]:
        return await _db_fn(client_id, entity_type, entity_id, _db_a, table_hint)

    @mcp.tool()
    async def log_read(
        client_id: str,
        query: str,
        log_posture: str,
        host: str | None = None,
        bucket: str | None = None,
        prefix: str | None = None,
    ) -> dict[str, Any]:
        return await _log_fn(
            client_id, query, log_posture, _log_a,
            host=host, bucket=bucket, prefix=prefix,
        )

    @mcp.tool()
    async def github_read(
        client_id: str,
        path: str,
        repo: str | None = None,
        ref: str = "main",
        org: str | None = None,
    ) -> dict[str, Any]:
        return await _gh_fn(client_id, path, repo=repo, ref=ref, org=org, github=_gh_a)

    @mcp.tool()
    async def history_search(
        client_id: str,
        query: str,
        top_k: int = 5,
        entity_type: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        return await _hist_fn(client_id, query, _vec_a, top_k, entity_type, domain)

    @mcp.tool()
    async def phoenix_resolve(
        client_id: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return await _phx_fn(client_id, force_refresh=force_refresh)

    return mcp


# ── Hook factory ──────────────────────────────────────────────────────────────

def make_agent_hooks(case: Any, audit_store: Any = None) -> dict[str, Any]:
    """
    Build PreToolUse / PostToolUse hook callbacks for the Agent SDK.

    The orchestrator (Prompt 6) calls this with the live Case and AuditStore.
    Returns a dict with "PreToolUse" and "PostToolUse" callables ready for the SDK.
    """
    from support_orchestration.tools.hooks import (
        ALLOWED_TOOLS,
        audit_read,
        block_writes,
        enforce_allowlist,
        enforce_client_scope,
    )

    def pre_tool_use(tool_name: str, tool_input: dict[str, Any]) -> None:
        block_writes(tool_name, tool_input)
        enforce_client_scope(tool_name, tool_input, case.client)
        enforce_allowlist(tool_name, ALLOWED_TOOLS)

    def post_tool_use(tool_name: str, tool_input: dict[str, Any], tool_output: object) -> None:
        audit_read(
            tool_name, tool_input, tool_output,
            case.case_id, case.client,
            audit_store=audit_store,
        )

    return {"PreToolUse": pre_tool_use, "PostToolUse": post_tool_use}
