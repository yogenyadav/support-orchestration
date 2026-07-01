"""history_search — vector search over embedded Jira history and Confluence docs.

The deflection engine: most incidents recur, and a history match is often the
fastest path to the right domain and blocker. Ranked by Haiku (C2, docs/4 §4.2).

Requires a vector store (Weaviate/pgvector/Pinecone — provisioned separately).
VectorStoreAdapter is injected; swap StubVectorAdapter in tests.
"""

from __future__ import annotations

from typing import Any

from .mcp_server import VectorStoreAdapter


async def history_search(
    client_id: str,
    query: str,
    vector: VectorStoreAdapter,
    top_k: int = 5,
    entity_type: str | None = None,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search past resolved incidents and Confluence pages for the closest matches.

    Args:
        client_id:   Scoped to this client (cross-client results filtered out).
        query:       Natural-language description of the current symptom.
        vector:      Injected adapter.
        top_k:       Number of results to return.
        entity_type: Optional filter ("order", "tote", ...).
        domain:      Optional filter ("WES", "WCS", ...).

    Returns list of dicts: [{"jira_id": ..., "summary": ..., "root_cause": ...,
                              "fix_summary": ..., "similarity": float}, ...]
    """
    filters: dict[str, Any] = {"client_id": client_id}
    if entity_type:
        filters["entity_type"] = entity_type
    if domain:
        filters["domain"] = domain

    results = await vector.search(query=query, top_k=top_k, filters=filters)
    return results
