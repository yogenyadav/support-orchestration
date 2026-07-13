"""github_read — read source code from base org and per-client org (read-only).

Both orgs must be readable to diagnose a real incident:
  - base org: shared code (transitions, triggers, state values)
  - client org: overlay (state-name overrides, custom config, schema specifics)

Agents read code to discover table names, column names, and state string values
at runtime — schema is never pre-loaded per docs/4 §4.11.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from support_orchestration.tools.mcp_server import GithubAdapter


async def github_read(
    client_id: str,
    path: str,
    *,
    repo: str | None = None,
    ref: str = "main",
    org: str | None = None,
    github: GithubAdapter,
) -> dict[str, Any]:
    """
    Read a file from GitHub (base org or client org).

    Args:
        client_id: Scopes the call; client org is derived from this.
        path:      File path within the repo (e.g. "src/wes/order_states.py").
        repo:      Repository name. Defaults to "core".
        ref:       Git ref (branch/tag/commit). Defaults to "main".
        org:       GitHub org. Defaults to client org; pass base org explicitly for shared code.
        github:    Adapter for the GitHub connection (injected; real in prod, stub in tests).
    """
    prefix = os.getenv("GITHUB_CLIENT_ORG_PREFIX", "client-")
    resolved_org = org or f"{prefix}{client_id}"
    resolved_repo = repo or "core"
    content = github.read_file(resolved_org, resolved_repo, path, ref)
    return {
        "content": content,
        "path": path,
        "org": resolved_org,
        "repo": resolved_repo,
        "ref": ref,
    }


def make_base_read(path: str, repo: str | None = None, ref: str = "main") -> dict[str, str]:
    """Helper: build kwargs for a base-org read."""
    base_org = os.environ.get("GITHUB_BASE_ORG", "base-org")
    return {"path": path, "repo": repo or "core", "ref": ref, "org": base_org}
