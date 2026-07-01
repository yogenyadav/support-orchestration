"""Fixture-backed adapter implementations for the eval harness.

Each class satisfies an adapter ABC from support_orchestration.tools.mcp_server
and returns canned responses drawn from the fixture's mocked_tool_responses block.

When a tool is absent from mocked_tool_responses, build_fixture_adapters falls
back to the corresponding Stub* adapter (empty/default responses).

These adapters are built per-fixture in run_eval() and will be wired into the
MCP server in Prompt 7 when subagent.diagnose() is implemented.
"""

from __future__ import annotations

from typing import Any

from support_orchestration.tools.mcp_server import (
    DbAdapter,
    GithubAdapter,
    LogAdapter,
    PhoenixAdapter,
    StubDbAdapter,
    StubGithubAdapter,
    StubLogAdapter,
    StubPhoenixAdapter,
    StubVectorAdapter,
    VectorStoreAdapter,
)


class FixtureDbAdapter(DbAdapter):
    """Returns canned DB rows matched by entity_type + entity_id."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def query(self, client_id: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        # db_state_read passes {"entity_id": ...} — no entity_type in params — so match by id only.
        eid = params.get("entity_id")
        for row in self._rows:
            if row.get("entity_id") == eid:
                return [row["response"]]
        return [self._rows[0]["response"]] if self._rows else []

    async def introspect_schema(self, client_id: str, table_hint: str) -> dict[str, Any]:
        return {}


class FixtureLogAdapter(LogAdapter):
    """Returns the first fixture log entry's response for any query."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    def _format_entries(self, entries: Any) -> str:
        if isinstance(entries, list):
            return "\n".join(str(e) for e in entries)
        return str(entries) if entries else ""

    async def read_direct(self, client_id: str, host: str, query: str) -> str:
        if self._entries:
            return self._format_entries(self._entries[0].get("response", {}).get("entries", ""))
        return ""

    async def read_s3(self, client_id: str, bucket: str, prefix: str, query: str) -> str:
        if self._entries:
            return self._format_entries(self._entries[0].get("response", {}).get("entries", ""))
        return ""


class FixtureVectorAdapter(VectorStoreAdapter):
    """Returns fixture-specified history search results."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    async def search(self, query: str, top_k: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        if self._results:
            return self._results[0].get("response", {}).get("results", [])
        return []


class FixtureGithubAdapter(GithubAdapter):
    """Returns fixture-specified file content matched by path."""

    def __init__(self, files: list[dict[str, Any]]) -> None:
        self._files: dict[str, str] = {
            f["path"]: f["response"].get("content", "") for f in files
        }

    def read_file(self, org: str, repo: str, path: str, ref: str = "main") -> str:
        return self._files.get(path, f"# no fixture content for {path}")


class FixturePhoenixAdapter(PhoenixAdapter):
    """Returns fixture-specified connectivity config for any client."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def resolve(self, client_id: str) -> dict[str, Any]:
        return self._config


def build_fixture_adapters(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Build adapters from the fixture's mocked_tool_responses block.

    Falls back to Stub* adapters for any tool not explicitly mocked.
    Returns a dict suitable for **-unpacking into build_mcp_server().
    """
    mocked: dict[str, Any] = fixture.get("mocked_tool_responses", {})

    db: DbAdapter = (
        FixtureDbAdapter(mocked["db_state_read"])
        if "db_state_read" in mocked
        else StubDbAdapter()
    )
    log: LogAdapter = (
        FixtureLogAdapter(mocked["log_read"])
        if "log_read" in mocked
        else StubLogAdapter()
    )
    vector: VectorStoreAdapter = (
        FixtureVectorAdapter(mocked["history_search"])
        if "history_search" in mocked
        else StubVectorAdapter()
    )
    github: GithubAdapter = (
        FixtureGithubAdapter(mocked["github_read"])
        if "github_read" in mocked
        else StubGithubAdapter()
    )
    phoenix_cfg = mocked.get("phoenix_resolve", {})
    phoenix: PhoenixAdapter = (
        FixturePhoenixAdapter(phoenix_cfg.get("response", {}))
        if phoenix_cfg
        else StubPhoenixAdapter()
    )

    return {"db": db, "log": log, "vector": vector, "github": github, "phoenix": phoenix}
