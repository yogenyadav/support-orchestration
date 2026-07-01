"""Tests for PgvectorStoreAdapter — vector search over incident history."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pgvector_import_error_on_missing_asyncpg(monkeypatch):
    """search() raises RuntimeError when asyncpg is not installed."""
    import builtins
    real_import = builtins.__import__

    from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
    adapter = PgvectorStoreAdapter("postgresql://localhost/test")

    def fake_import(name: str, *args, **kwargs):
        if name == "asyncpg":
            raise ImportError("No module named 'asyncpg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    adapter._pool = None  # force re-import

    with pytest.raises(RuntimeError, match="asyncpg"):
        await adapter.search("query", 5, {})


@pytest.mark.asyncio
async def test_fulltext_search_builds_correct_query():
    """Without an embed_fn, the adapter uses ILIKE full-text search."""
    from unittest.mock import AsyncMock, MagicMock

    mock_rows = [
        {"jira_id": "WH-1", "summary": "Order stuck", "root_cause": "Service down",
         "fix_summary": "Restart", "similarity": 0.5},
    ]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=mock_rows)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
    adapter = PgvectorStoreAdapter("postgresql://localhost/test")
    adapter._pool = mock_pool  # inject pool directly to bypass asyncpg.create_pool

    results = await adapter.search(
        query="picking engine not acking",
        top_k=3,
        filters={"client_id": "acme"},
    )

    assert results == mock_rows
    # Verify the SQL call happened with the right args
    mock_conn.fetch.assert_called_once()
    call_args = mock_conn.fetch.call_args[0]
    assert "ILIKE" in call_args[0]


@pytest.mark.asyncio
async def test_fulltext_search_with_multiple_filters():
    """Filters are applied as WHERE conditions."""
    from unittest.mock import AsyncMock, MagicMock

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
    adapter = PgvectorStoreAdapter("postgresql://localhost/test")
    adapter._pool = mock_pool

    await adapter.search(
        query="order stuck",
        top_k=5,
        filters={"client_id": "acme", "domain": "WES"},
    )

    call_sql = mock_conn.fetch.call_args[0][0]
    assert "WHERE" in call_sql


@pytest.mark.asyncio
async def test_vector_search_calls_embed_fn():
    """With embed_fn provided, the adapter calls it and uses vector similarity."""
    from unittest.mock import AsyncMock, MagicMock

    embed_called_with: list[str] = []

    async def mock_embed(text: str) -> list[float]:
        embed_called_with.append(text)
        return [0.1] * 1536

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
    adapter = PgvectorStoreAdapter(
        "postgresql://localhost/test",
        embed_fn=mock_embed,
    )
    adapter._pool = mock_pool

    await adapter.search("test query", top_k=3, filters={})

    assert embed_called_with == ["test query"]
    call_sql = mock_conn.fetch.call_args[0][0]
    assert "<=>" in call_sql   # cosine distance operator


@pytest.mark.asyncio
async def test_search_no_filters():
    """Search with empty filters runs without WHERE clause."""
    from unittest.mock import AsyncMock, MagicMock

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    from support_orchestration.tools.adapters.vector_adapter import PgvectorStoreAdapter
    adapter = PgvectorStoreAdapter("postgresql://localhost/test")
    adapter._pool = mock_pool

    results = await adapter.search("anything", top_k=5, filters={})
    assert results == []
