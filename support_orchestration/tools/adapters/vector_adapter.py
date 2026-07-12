"""PgvectorStoreAdapter — vector search over Jira history + Confluence embeddings.

Uses PostgreSQL with the pgvector extension. The `embed_fn` converts query text
to a float vector; if omitted, falls back to PostgreSQL full-text search (ilike).

Expected table schema (create once at provisioning time):

    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS incident_embeddings (
        id            SERIAL PRIMARY KEY,
        jira_id       VARCHAR(50)   NOT NULL,
        client_id     VARCHAR(100)  NOT NULL,
        entity_type   VARCHAR(50),
        domain        VARCHAR(50),
        summary       TEXT,
        root_cause    TEXT,
        fix_summary   TEXT,
        embedding     vector(1024),  -- voyage-3 output dimension (Voyage AI)
        created_at    TIMESTAMPTZ DEFAULT NOW()
    );

    ALTER TABLE incident_embeddings ADD CONSTRAINT incident_embeddings_jira_id_key UNIQUE (jira_id);
    CREATE INDEX ON incident_embeddings USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    CREATE INDEX ON incident_embeddings (client_id);

Install: pip install asyncpg pgvector
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from support_orchestration.tools.mcp_server import VectorStoreAdapter

EmbedFn = Callable[[str], Coroutine[Any, Any, list[float]]]


class PgvectorStoreAdapter(VectorStoreAdapter):
    """
    pgvector-backed semantic search over resolved incidents and Confluence docs.

    Args:
        dsn:      asyncpg DSN: postgresql://user:pass@host:port/db
        table:    Table name (default: incident_embeddings).
        embed_fn: Async callable that embeds a query string → list[float].
                  If None, falls back to PostgreSQL full-text (ILIKE) search.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "incident_embeddings",
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._dsn = dsn
        self._table = table
        self._embed_fn = embed_fn
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError(
                    "asyncpg is required for vector store. Install: pip install asyncpg pgvector"
                ) from exc
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)
        return self._pool

    async def search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        client_id = filters.get("client_id")
        entity_type = filters.get("entity_type")
        domain = filters.get("domain")

        where_parts = []
        args: list[Any] = []

        if client_id:
            args.append(client_id)
            where_parts.append(f"client_id = ${len(args)}")
        if entity_type:
            args.append(entity_type)
            where_parts.append(f"entity_type = ${len(args)}")
        if domain:
            args.append(domain)
            where_parts.append(f"domain = ${len(args)}")

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        async with pool.acquire() as conn:
            if self._embed_fn is not None:
                return await self._vector_search(conn, query, top_k, where_sql, args)
            return await self._fulltext_search(conn, query, top_k, where_sql, args)

    async def _vector_search(
        self,
        conn: Any,
        query: str,
        top_k: int,
        where_sql: str,
        args: list[Any],
    ) -> list[dict[str, Any]]:
        assert self._embed_fn is not None
        embedding = await self._embed_fn(query)
        embedding_arg_idx = len(args) + 1
        top_k_arg_idx = len(args) + 2

        sql = f"""
            SELECT jira_id, summary, root_cause, fix_summary,
                   1 - (embedding <=> ${embedding_arg_idx}::vector) AS similarity
            FROM {self._table}
            {where_sql}
            ORDER BY embedding <=> ${embedding_arg_idx}::vector
            LIMIT ${top_k_arg_idx}
        """
        rows = await conn.fetch(sql, *args, embedding, top_k)
        return [dict(r) for r in rows]

    async def _fulltext_search(
        self,
        conn: Any,
        query: str,
        top_k: int,
        where_sql: str,
        args: list[Any],
    ) -> list[dict[str, Any]]:
        query_arg_idx = len(args) + 1
        top_k_arg_idx = len(args) + 2
        like_query = f"%{query}%"

        sql = f"""
            SELECT jira_id, summary, root_cause, fix_summary, 0.5 AS similarity
            FROM {self._table}
            {where_sql}
            {"AND" if where_sql else "WHERE"} (
                summary ILIKE ${query_arg_idx}
                OR root_cause ILIKE ${query_arg_idx}
                OR fix_summary ILIKE ${query_arg_idx}
            )
            ORDER BY created_at DESC
            LIMIT ${top_k_arg_idx}
        """
        rows = await conn.fetch(sql, *args, like_query, top_k)
        return [dict(r) for r in rows]

    async def write(self, record: dict[str, Any]) -> None:
        """Upsert a resolved incident into the vector store.

        Embeds summary + root_cause + fix_summary (if embed_fn provided) and
        upserts on jira_id, so re-resolving the same ticket overwrites the row.
        """
        pool = await self._get_pool()

        embedding: list[float] | None = None
        if self._embed_fn is not None:
            text = " ".join(filter(None, [
                record.get("summary"),
                record.get("root_cause"),
                record.get("fix_summary"),
            ]))
            if text:
                embedding = await self._embed_fn(text)

        sql = f"""
            INSERT INTO {self._table}
                (jira_id, client_id, entity_type, domain, summary, root_cause, fix_summary, embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (jira_id) DO UPDATE SET
                root_cause  = EXCLUDED.root_cause,
                fix_summary = EXCLUDED.fix_summary,
                embedding   = EXCLUDED.embedding,
                created_at  = NOW()
        """
        async with pool.acquire() as conn:
            await conn.execute(
                sql,
                record.get("jira_id"),
                record.get("client_id"),
                record.get("entity_type"),
                record.get("domain"),
                record.get("summary"),
                record.get("root_cause"),
                record.get("fix_summary"),
                embedding,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
