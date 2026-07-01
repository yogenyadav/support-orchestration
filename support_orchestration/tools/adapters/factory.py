"""Adapter factory — build all production adapters from environment variables.

Call build_adapters_from_env() at startup to get a dict of configured adapters
ready to pass to build_mcp_server(). Adapters for optional features (DB, SSH)
are only built when the required env vars are present.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from support_orchestration.tools.adapters.github_adapter import GithubApiAdapter
from support_orchestration.tools.adapters.phoenix_adapter import HttpPhoenixAdapter
from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
from support_orchestration.tools.mcp_server import (
    GithubAdapter,
    PhoenixAdapter,
    StubDbAdapter,
    StubLogAdapter,
    StubVectorAdapter,
    VectorStoreAdapter,
)

logger = logging.getLogger(__name__)


def build_adapters_from_env() -> dict[str, Any]:
    """
    Build production adapters from environment variables.

    Returns a dict with keys: db, log, vector, phoenix, github.
    Falls back to stub adapters when required env vars are absent
    (so startup never fails in test/dev mode).

    Expected env vars:
        GITHUB_TOKEN                 — required for GithubApiAdapter
        PHOENIX_BASE_URL             — required for HttpPhoenixAdapter
        PHOENIX_API_TOKEN            — required for HttpPhoenixAdapter
        VECTOR_STORE_DSN             — required for PgvectorStoreAdapter
                                       (format: postgresql://user:pass@host/db)
        # DB and SSH adapters are built on a per-incident basis from Phoenix
        # connectivity details — see orchestrator._build_client_adapters()
    """
    adapters: dict[str, Any] = {}

    # GitHub adapter
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        prefix = os.environ.get("GITHUB_CLIENT_ORG_PREFIX", "client-")
        adapters["github"] = GithubApiAdapter(token=token, client_org_prefix=prefix)
        logger.info("GithubApiAdapter configured")
    else:
        adapters["github"] = None
        logger.warning("GITHUB_TOKEN not set — using StubGithubAdapter")

    # Phoenix adapter
    phoenix_url = os.environ.get("PHOENIX_BASE_URL", "")
    phoenix_token = os.environ.get("PHOENIX_API_TOKEN", "")
    if phoenix_url and phoenix_token:
        adapters["phoenix"] = HttpPhoenixAdapter(base_url=phoenix_url, api_token=phoenix_token)
        logger.info("HttpPhoenixAdapter configured → %s", phoenix_url)
    else:
        adapters["phoenix"] = None
        logger.warning("PHOENIX_BASE_URL/PHOENIX_API_TOKEN not set — using StubPhoenixAdapter")

    # Vector store adapter
    vector_dsn = os.environ.get("VECTOR_STORE_DSN", "")
    if vector_dsn:
        adapters["vector"] = PgvectorStoreAdapter(dsn=vector_dsn)
        logger.info("PgvectorStoreAdapter configured")
    else:
        adapters["vector"] = None
        logger.warning("VECTOR_STORE_DSN not set — using StubVectorAdapter")

    # DB and log adapters: built per-incident from Phoenix connectivity details.
    # Pass None here; the orchestrator builds them after calling phoenix_resolve.
    adapters["db"] = None
    adapters["log"] = None

    return adapters


def build_mcp_server_from_env() -> Any:
    """Build an MCP server with production adapters sourced from env vars."""
    from support_orchestration.tools.mcp_server import build_mcp_server

    a = build_adapters_from_env()
    return build_mcp_server(
        db=a["db"],
        log=a["log"],
        vector=a["vector"],
        phoenix=a["phoenix"],
        github=a["github"],
    )
