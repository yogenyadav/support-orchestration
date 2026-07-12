# Vector Databases — Concepts, Design, and Implementation

A reference covering everything you need to understand why and how this system uses pgvector: what a vector database is, who creates the vectors, how LLMs and agents use them, how queries are made fast, why we chose pgvector, how much it costs, and exactly how all the pieces wire together in this codebase.

---

## Table of Contents

1. [What is a Vector Database?](#1-what-is-a-vector-database)
2. [The Embedding Model — Who Creates the Vectors?](#2-the-embedding-model--who-creates-the-vectors)
3. [How LLMs Use Vector Databases — Standard RAG](#3-how-llms-use-vector-databases--standard-rag)
4. [How Agents Use Vector Databases — Agentic RAG](#4-how-agents-use-vector-databases--agentic-rag)
5. [When to Add a Vector DB to Your Architecture](#5-when-to-add-a-vector-db-to-your-architecture)
6. [How Vector DBs Make Queries Fast — HNSW](#6-how-vector-dbs-make-queries-fast--hnsw)
7. [Why pgvector Over a Standalone Vector DB](#7-why-pgvector-over-a-standalone-vector-db)
8. [pgvector vs. Other Open-Source Vector Databases](#8-pgvector-vs-other-open-source-vector-databases)
9. [Cost Model](#9-cost-model)
10. [Embedding Model Decision — Voyage AI voyage-3](#10-embedding-model-decision--voyage-ai-voyage-3)
11. [How It All Works in This System](#11-how-it-all-works-in-this-system)
12. [Decisions Made](#12-decisions-made)

---

## 1. What is a Vector Database?

### The problem with keyword search

A traditional database stores text and finds it by exact or fuzzy match:

```sql
SELECT * FROM incidents WHERE summary LIKE '%timeout%'
```

This works when you know the exact words. It breaks when the same problem is described differently across incidents:

| Incident | Description |
|---|---|
| WH-1234 | "consumer ack timeout" |
| WH-5678 | "WES dropped connection to picking engine" |
| WH-9012 | "release never acknowledged, order stuck" |

All three describe the same root cause — a lost acknowledgment in the WES consumer — but a keyword search for "ack timeout" finds only the first one.

### Embeddings: converting meaning to numbers

An **embedding model** converts text into an array of numbers — a **vector** — where similar meaning produces vectors that point in similar directions in high-dimensional space.

```
"consumer ack timeout"          → [0.23, -0.41, 0.87, 0.12, ...]  (1024 floats)
"WES dropped connection"        → [0.21, -0.39, 0.84, 0.14, ...]  (very close — same meaning)
"printer hardware fault"        → [-0.71, 0.12, -0.33, 0.56, ...]  (far away — different topic)
```

A vector database stores these number arrays alongside the original text and lets you search by **semantic similarity** — find text with the same meaning, regardless of the words used. Two sentences about the same concept land near each other in vector space; unrelated sentences land far apart.

### What are the dimensions?

The 1024 numbers are **abstract learned features** — not labelled columns. They are the output of a transformer neural network trained on billions of documents. The model learned to compress meaning into these 1024 axes during training. You cannot read them or label them. Dimension 417 might loosely correlate with "connectivity issues" but this is not guaranteed or interpretable.

**Critically:** dimensions are NOT slots for your business data. The client, domain, or incident severity are NOT encoded in the vector. They live as separate columns in the table and are filtered with normal SQL.

### What's stored in a vector DB row?

Both the original text AND the vector live in the same row:

```
jira_id   | client_id | domain | summary (TEXT)                  | embedding (vector(1024))
----------+-----------+--------+---------------------------------+-------------------------
WH-1234   | ACME      | WES    | "Order stuck at prioritized..." | [0.23, -0.41, 0.87, ...]
WH-5678   | ACME      | WES    | "Consumer ack dropped..."       | [0.21, -0.39, 0.84, ...]
```

The text is what gets returned to the LLM. The vector is used only for the similarity ranking. The LLM never sees the numbers.

### Similarity: cosine distance

Vectors are compared using **cosine distance** — the angle between two vectors, not their length. This matters because a short incident description and a long one about the same problem should be treated as similar. Euclidean distance would penalize the length difference; cosine ignores it.

In pgvector, cosine distance is the `<=>` operator:

```sql
ORDER BY embedding <=> $query_vector   -- smallest angle = most similar
```

A similarity score of 1.0 means identical meaning; 0.0 means completely unrelated. We compute `1 - distance` to get a 0–1 similarity score where 1.0 is perfect.

---

## 2. The Embedding Model — Who Creates the Vectors?

### The four distinct roles

This is the most important thing to understand before going further:

| Component | Role | Who pays |
|---|---|---|
| **Embedding model** (Voyage AI voyage-3) | Converts text → 1024 floats | You pay Voyage AI per API call |
| **Python agent code** | Calls the embedding model, stores to pgvector, queries pgvector, formats text for LLM | Your server compute |
| **pgvector / Postgres** | Stores + indexes vectors, runs HNSW similarity search | Your Postgres server (free beyond infra) |
| **LLM (Claude)** | Reads text only, produces diagnosis text | You pay Anthropic per token |

The embedding model is a completely different type of model from the LLM. Claude (Haiku/Sonnet/Opus) is a generative model: it reads text, produces text. An embedding model is a specialized encoder: it reads text, produces a fixed-length array of numbers. They are separate services, separate API calls, separate bills.

### Dimensions are set by the model, not by you

When you choose an embedding model, the dimension is a fixed property of that model's architecture — the size of its output layer. You cannot configure it.

| Embedding model | Dimension | Cost per 1M tokens |
|---|---|---|
| Voyage AI `voyage-3` | **1024** | $0.06 |
| OpenAI `text-embedding-3-large` | 1536 | $0.13 |
| Google `text-embedding-004` | 768 | $0.025 |
| Cohere `embed-v3` | 1024 | $0.10 |

You pick the model → you get that model's dimension, always. When we put `vector(1024)` in the schema, we are saying "we will use a model that outputs 1024 floats." If you switch embedding models, you must rebuild the entire index with the new dimension.

### Calling the embedding model from Python

In this system, the embedding function is injected as `embed_fn` into `PgvectorStoreAdapter`. The caller provides it:

```python
import voyageai

voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

async def embed(text: str) -> list[float]:
    result = await asyncio.to_thread(
        voyage_client.embed, [text], model="voyage-3"
    )
    return result.embeddings[0]   # always 1024 floats

adapter = PgvectorStoreAdapter(dsn=DATABASE_URL, embed_fn=embed)
```

You pass text → Voyage AI returns exactly 1024 floats, always. pgvector doesn't know what Voyage AI is; it just receives a list of numbers.

### The LLM never sees a vector

The complete pipeline shows where the LLM fits (at the end, reading only text):

```
Python agent code
  │
  ├── calls embed_fn("consumer ack timeout")
  │        ↓ Voyage AI API call (you pay here)
  │        ← [0.23, -0.41, 0.87, ...] (1024 floats)
  │
  ├── stores in pgvector (free — your Postgres)
  │        row: jira_id | client_id | summary TEXT | embedding vector(1024)
  │
  ├── queries pgvector (free — your Postgres)
  │        SQL: WHERE client_id = 'ACME'         ← SQL filter first
  │             ORDER BY embedding <=> $query_vec  ← then vector ranking
  │        returns: text columns + similarity score
  │
  └── formats results as plain text
             ↓
           LLM prompt  ← Claude sees ONLY this:

           "Similar past incidents:
            1. (WH-1234, similarity 0.91) Order stuck at prioritized.
               Root cause: consumer ack timeout.
               Fix: restart consumer service.
            2. (WH-5678, similarity 0.84) ..."
             ↓
           Claude reads text, produces text diagnosis.
           It does not know pgvector or Voyage AI exist.
```

---

## 3. How LLMs Use Vector Databases — Standard RAG

### The LLM's two fundamental limitations

1. **Knowledge cutoff** — the model knows nothing about events after its training data was collected.
2. **Context window** — the model can only reason over a limited amount of text at once. You cannot stuff 125 clients × years of incident history into one prompt.

### RAG: Retrieval-Augmented Generation

RAG solves both limitations by retrieving relevant information at inference time and injecting it as context into the LLM prompt:

```
Step 1: New incident arrives
        description: "Order WH-9912 stuck at prioritized for ACME"

Step 2: Embed the description → query vector
        embed_fn("Order stuck at prioritized...") → [0.23, -0.41, ...]

Step 3: Search pgvector for the top-5 most similar past incidents
        SQL: WHERE client_id = 'ACME' ORDER BY embedding <=> $query_vec LIMIT 5

Step 4: Inject the returned text into the LLM prompt as context
        "Here are 5 similar past incidents resolved at ACME:
         1. WH-1234: consumer ack timeout → restart consumer (fix SQL: UPDATE orders...)
         2. ..."

Step 5: LLM reasons over the retrieved material
        → produces diagnosis grounded in past resolutions
```

The LLM doesn't "know" these incidents from training. It reads them as text handed to it at inference time — like an engineer reading case notes before a diagnosis call.

**Standard RAG is read-only.** The corpus is static; the LLM only consumes it. This is how most RAG systems work.

---

## 4. How Agents Use Vector Databases — Agentic RAG

### Standard RAG vs. Agentic RAG

Standard RAG: read only. Corpus is static. Doesn't improve over time.

Agentic RAG: read + write-back. After an agent resolves a task, it writes a structured memory record back into the corpus. The corpus grows with every resolution — the system gets smarter with every ticket closed.

### The write-back loop

```
New incident
  │
  ├── history_search (retrieval)
  │     embed incident description → find top-5 similar past incidents
  │     inject as context into subagent prompt
  │
  └── agent diagnoses → human applies fix → /approve
                │
                └── _write_resolution()
                      │
                      ├── _formulate_memory() → Haiku (C9)
                      │     input:  raw diagnosis + fix text
                      │     output: structured 4-line memory block
                      │             **Context**: Order stuck at prioritized in WES.
                      │             **Root Cause**: Consumer ack dropped.
                      │             **Resolution**: Restarted consumer service.
                      │             **Watch Out For**: Repeat during high throughput.
                      │
                      └── VectorStoreAdapter.write()
                            embed the 4-line block → 1024 floats
                            upsert into pgvector ON CONFLICT (jira_id) DO UPDATE
```

The next time a similar incident arrives, `history_search` retrieves this resolved incident and the agent has a running start.

### Why structure the memory block?

Raw diagnosis prose embeds inconsistently — different phrasings of the same problem land in different regions of vector space. The four-field format (**Context / Root Cause / Resolution / Watch Out For**) is consistent every time. Embedding the same structure produces tight clusters: semantically similar incidents land close together, and retrieval is reliable.

Haiku is used for this (C9) because it's a narrow, structured task — expensive models are not needed. `max_tokens=200`, the prompt enforces the four-line format.

### The multi-agent hive mind

All 9 domain subagents (WES, GTP_PICKING, GTP_DECANT, IMS, ASRS, LPN, WCS, infra, ESB) share one vector corpus with no agent-name filter on reads. A WES subagent resolving a consumer-timeout incident enriches the corpus. The next time any subagent encounters a similar symptom — even a different domain subagent — that WES resolution surfaces in `history_search`. One agent's learning is available to all.

### Non-fatal design

Vector write-back is wrapped in `try/except`. If pgvector is down:

```python
try:
    memory_summary = await self._formulate_memory(diagnosis_summary, fix_summary)
    await self._vector.write({...})
except Exception:
    logger.warning("Vector write-back failed for %s (non-fatal)", self.case.jira_ticket_id)
    # continue — Jira write already done
```

Jira is the system of record. Vector failure is a degraded-mode issue (future retrieval quality degrades slightly) but never an abort condition. The engineer's resolution is already in Jira.

---

## 5. When to Add a Vector DB to Your Architecture

### Add it when:

| Signal | Reason |
|---|---|
| Corpus > ~1,000 docs and growing | Keyword search degrades as it grows; semantic search doesn't |
| Domain jargon and synonyms | "ack timeout" = "connection dropped" = "consumer down" — keyword search misses these |
| Knowledge doesn't fit in the context window | 125 clients × years of incidents can't be injected at once |
| Past interactions should inform future ones | Incidents repeat; deflection via history match is the fastest path to a fix |
| Multiple agents working the same problem space | Shared corpus = collective memory, zero additional effort per agent |

### Skip it when:

| Signal | Reason |
|---|---|
| Small, stable corpus (< a few hundred docs) | Just put it in the system prompt — simpler, no infra overhead |
| Purely structured queries | SQL with indexes is faster and simpler |
| Exact-match retrieval (IDs, serial numbers) | A hash index in Postgres beats vectors here |
| Early prototype, uncertain value | Add it when the corpus exists and has proven value |

### In this project

The case is clear:
- 125 clients × years of resolved incidents = large, growing corpus
- Warehouse domain jargon varies across clients and across time
- History doesn't fit in a single prompt
- Incidents repeat in patterns (WES consumer timeouts appear repeatedly across clients)
- 9 agents benefit from a shared corpus without any per-agent coordination

---

## 6. How Vector DBs Make Queries Fast — HNSW

### The naive approach

Compare the query vector against every stored vector and return the k closest. This is O(n) — brute force. At 1,000 vectors: milliseconds. At 1,000,000 vectors: seconds. Not acceptable in a production support system.

### ANN: Approximate Nearest Neighbor

Trade a small amount of recall accuracy for massive speed gains. Instead of finding the *exact* nearest neighbor, find one that is *very close* to exact — in practice, indistinguishable for text retrieval at our scale.

### HNSW: Hierarchical Navigable Small World

HNSW is the dominant ANN algorithm today. It builds a multi-layer graph that functions like a highway system:

```
Layer 2 — express routes (few nodes, large jumps across the space):
  A ─────────────────────────────────→ J

Layer 1 — arterial roads (more nodes, medium jumps):
  A ──────────→ E ──────────→ J

Layer 0 — local streets (all nodes, dense connections):
  A → B → C → D → E → F → G → H → I → J
```

**To find the nearest neighbor to query Q:**
1. Enter at the top layer. Greedily move toward Q with large jumps.
2. Drop down a layer. Refine with smaller jumps.
3. Repeat until Layer 0. Do a precise local search among nearby nodes.
4. Return the closest node found.

This is analogous to: *which country? → which city? → which street? → which building?*. Each layer narrows the search space before the next. You skip almost all of the data.

### Our HNSW settings

```sql
CREATE INDEX ON incident_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

**`m = 16`** — each node connects to 16 neighbors per layer. This controls the density of the graph:
- Lower m (e.g., 8): faster build, less memory, lower recall
- Higher m (e.g., 32): slower build, more memory, higher recall
- 16 is the standard starting point for 1024-dimensional embeddings: balanced recall and memory cost

**`ef_construction = 64`** — during index build, how many candidate nodes to explore before finalizing each node's connections:
- Lower (e.g., 32): faster build, slightly lower recall at query time
- Higher (e.g., 128): slower build, better recall
- 64 is a conservative production default: reliable recall without expensive build times

**`ef_search`** (set at query time, not index time) — how many candidates to explore during a search query. We use the default (40). Increase this if recall quality needs to improve at the cost of slightly slower queries.

**Result:** millisecond-scale search over millions of 1024-dimensional vectors.

### Why cosine distance, not Euclidean?

Cosine distance measures the **angle** between two vectors — the semantic direction — not their magnitude (length). Two incident descriptions about the same problem but different lengths (one terse, one verbose) should be treated as similar. Euclidean distance penalizes the length difference; cosine ignores it.

For text embeddings, cosine is the universal standard. The `vector_cosine_ops` index class and the `<=>` operator implement this in pgvector.

### IVFFlat (deprecated — do not use)

The older algorithm. Partitions vectors into clusters via k-means at index build time; at query time, searches only the nearest cluster. Simpler to build but systematically misses neighbors that live near a cluster boundary. HNSW dominates in practice. We explicitly switched from IVFFlat to HNSW when building the write-back feature.

---

## 7. Why pgvector Over a Standalone Vector DB

### Already on Postgres

This system uses Postgres for the state store (`CaseStore`) and audit store. pgvector is an extension that adds vector search to the same Postgres instance. No additional service to deploy, monitor, back up, or migrate.

### ACID compliance

9 concurrent subagents can resolve incidents simultaneously, each calling `_write_resolution()`. Postgres handles concurrent writes with row-level locking and full ACID transaction semantics. Many standalone vector databases (Chroma especially, Weaviate partially) have weaker or eventual-consistency semantics. For an audit-grade system supporting 125 client warehouses, ACID is not optional.

### Relational + vector in one query

The strongest reason. Our `history_search` first filters by `client_id` — a normal SQL WHERE clause — before the vector similarity search. This instantly shrinks the candidate pool from all incidents to one client's incidents, then runs HNSW only over that filtered set:

```sql
SELECT jira_id, summary, root_cause, fix_summary,
       1 - (embedding <=> $1::vector) AS similarity
FROM incident_embeddings
WHERE client_id = $2                        -- hard SQL filter runs first (B-tree index)
  AND ($3::text IS NULL OR entity_type = $3) -- optional entity_type filter
  AND ($4::text IS NULL OR domain = $4)      -- optional domain filter
ORDER BY embedding <=> $1::vector            -- HNSW similarity search on filtered set
LIMIT $5;
```

This is one database query. With a separate vector database, you'd either make two round trips (SQL filter + vector search) or push complex pre-filtering logic into the vector DB's API. pgvector makes this natural.

### Operational simplicity

One backup target. One monitoring config. One schema migration path. One `psql` session for debugging. For a PoC supporting 125 clients, introducing a second database service doubles the operational surface.

### Scale ceiling

pgvector is appropriate up to approximately 10 million vectors. At 125 clients with years of incident history, a generous estimate is 50,000–200,000 resolved incidents total — well within the sweet spot. If this system eventually supports millions of clients and requires GPU-accelerated search at high query-per-second rates, the migration path to Qdrant or Milvus is well-defined. That is a future problem.

---

## 8. pgvector vs. Other Open-Source Vector Databases

| DB | Strengths | Weaknesses | Best for |
|---|---|---|---|
| **pgvector** | ACID, relational SQL + vector in one query, operational simplicity, one service | Not GPU-accelerated; ~10M vector sweet spot | Already on Postgres; multi-attribute filtering; moderate scale |
| **Chroma** | Zero-config, embedded Python library, instant prototyping | No ACID, not production-hardened, no clustering | Rapid prototyping, notebooks, local experimentation |
| **Qdrant** | Rust-based, very fast, REST/gRPC, rich payload filtering API | Separate service to deploy and operate | High-QPS production with complex metadata filtering |
| **Weaviate** | Auto-vectorization modules, BM25+vector hybrid search, ecosystem | Heavy dependencies, steep learning curve | Teams wanting a full ML platform, not just storage |
| **Milvus** | Distributed, enterprise-scale, GPU support, billions of vectors | Overkill for single-system; significant operational overhead | 100M+ vector corpora, dedicated ML infra teams |
| **LanceDB** | File-based, serverless, zero infra overhead | Immature ecosystem, smaller community | Serverless / edge / embedded scenarios |

**Decision rule:** Start with pgvector if you already have Postgres. Vector search quality is equivalent to standalone databases at our scale. Add operational complexity only when you have outgrown it — either at 10M+ vectors or when QPS demands dedicated GPU-backed indexing.

---

## 9. Cost Model

### What is free

Every operation that runs inside your own Postgres instance costs nothing beyond the compute you already pay for:

- Storing a vector row in pgvector: **free**
- Running an HNSW similarity search: **free**
- Reading rows back from pgvector: **free**

### What you pay for

The only external API call you pay for is `embed_fn(text)` — every time Python calls out to Voyage AI to convert a string into a 1024-float vector. This happens **exactly twice per incident lifecycle**:

| When | What you embed | Approx. tokens | Cost |
|---|---|---|---|
| **At write-back** (after resolution) | The 4-line Haiku-formulated memory block | ~150 tokens | $0.000009 |
| **At search** (background prep) | The incident description query | ~80 tokens | $0.0000048 |

At Voyage AI's rate of $0.06 per 1 million tokens:

```
50 resolutions/day × 150 tokens  =  7,500 tokens/day  →  $0.00045/day
200 searches/day   ×  80 tokens  = 16,000 tokens/day  →  $0.00096/day
─────────────────────────────────────────────────────────────────────
Total:                                                 ~$0.0014/day
                                                       ~$0.042/month
```

**Embedding cost for this system is approximately 5 cents per month.** It is negligible compared to LLM inference costs.

### The Haiku call (C9) is a separate cost

`_formulate_memory()` calls Haiku to structure the memory block before embedding it. This is a separate Anthropic charge (not Voyage AI):

- ~200 input tokens + ~150 output tokens per resolution
- At Haiku's rate of $1/$5 per million tokens: ~$0.00095 per resolution
- At 50 resolutions/day: ~$0.048/day → ~$1.43/month

Still very cheap. The full embedding + memory-formulation cost per incident is under a fraction of a cent.

---

## 10. Embedding Model Decision — Voyage AI voyage-3

**Decision: Voyage AI `voyage-3`, 1024 dimensions.**

### Why Voyage AI

Anthropic explicitly recommends Voyage AI as the embedding partner for Claude-based systems. voyage-3 was designed to complement Claude's reasoning patterns — the semantic space it produces aligns well with the kind of technical reasoning Claude does during diagnosis.

Additional reasons:

- **Cost:** $0.06/1M tokens — half the cost of OpenAI's text-embedding-3-large ($0.13/1M)
- **Quality on technical text:** strong on software engineering and operational content, which is exactly what warehouse incident descriptions are
- **Single vendor for Claude + embeddings:** Anthropic/Voyage AI is a coherent pairing; OpenAI would add a second vendor account for marginal quality gain

### Why not the others

| Alternative | Reason not chosen |
|---|---|
| OpenAI `text-embedding-3-large` | Adds an OpenAI dependency to a system otherwise entirely Anthropic; costs more than Voyage AI for equivalent quality at this scale |
| Google `text-embedding-004` | Pulls in a GCP/Vertex AI dependency for a system not otherwise GCP-dependent |
| Cohere `embed-v3` | No advantage over Voyage AI here; separate account and billing |

### Schema implication

The schema declares `vector(1024)` to match voyage-3's output dimension:

```sql
embedding vector(1024)   -- Voyage AI voyage-3 output dimension
```

This is in `support_orchestration/tools/adapters/vector_adapter.py`. If the embedding model is ever changed, this column definition must be updated and the index rebuilt.

---

## 11. How It All Works in This System

### The schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS incident_embeddings (
    id          SERIAL PRIMARY KEY,
    jira_id     VARCHAR(50)   NOT NULL,        -- upsert key: globally unique
    client_id   VARCHAR(100)  NOT NULL,        -- SQL filter for per-client isolation
    entity_type VARCHAR(50),                   -- SQL filter: order | tote
    domain      VARCHAR(50),                   -- SQL filter: WES | WCS | IMS ...
    summary     TEXT,                          -- the Haiku-formulated 4-line memory block
    root_cause  TEXT,                          -- raw root cause text
    fix_summary TEXT,                          -- raw fix description
    embedding   vector(1024),                  -- Voyage AI voyage-3 output (NULL if no embed_fn)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Idempotent upsert: re-resolving the same ticket overwrites the row
ALTER TABLE incident_embeddings
    ADD CONSTRAINT incident_embeddings_jira_id_key UNIQUE (jira_id);

-- HNSW index for fast semantic search (cosine distance)
CREATE INDEX ON incident_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree index for fast SQL pre-filtering by client
CREATE INDEX ON incident_embeddings (client_id);
```

### Write-back flow (after /approve)

```
Human types /approve in Teams
  │
  ▼
Orchestrator._write_resolution()                     [orchestrator/orchestrator.py]
  │
  ├── 1. Write Jira resolution record                [glue/jira.py]
  │         AtlassianJiraClient.write_resolution()
  │         → adds comment to Jira ticket
  │         → attempts resolve transition (best-effort)
  │
  └── 2. Vector write-back (non-fatal)
          │
          ├── _formulate_memory(diagnosis_summary, fix_summary)
          │       calls claude-haiku-4-5 (C9, max_tokens=200)
          │       system prompt: "Output exactly four labelled lines..."
          │       returns:
          │         "**Context**: Order stuck at prioritized in WES.
          │          **Root Cause**: Consumer ack dropped due to timeout.
          │          **Resolution**: Restarted consumer and re-emitted release.
          │          **Watch Out For**: Repeat during high-throughput periods."
          │
          └── VectorStoreAdapter.write(record)       [tools/adapters/vector_adapter.py]
                  text = summary + root_cause + fix_summary  (~150 tokens)
                  embedding = await embed_fn(text)           ← Voyage AI API call
                  SQL: INSERT INTO incident_embeddings (...)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT (jira_id) DO UPDATE SET
                           root_cause  = EXCLUDED.root_cause,
                           fix_summary = EXCLUDED.fix_summary,
                           embedding   = EXCLUDED.embedding,
                           created_at  = NOW()
```

### Retrieval flow (background prep — C7)

```
New incident assigned in Jira
  │
  ▼
BackgroundPrepRunner (asyncio.gather — runs in parallel with phoenix_resolve + entity classify)
  │
  └── history_search(                               [tools/history_retrieval.py]
          client_id = case.client,
          query     = case.description,             -- raw Jira summary + background
          top_k     = 5
      )
        │
        ├── embed_fn(query)                         ← Voyage AI API call (~80 tokens)
        │     returns query_vector [1024 floats]
        │
        └── PgvectorStoreAdapter.search()           [tools/adapters/vector_adapter.py]
              SQL:
                SELECT jira_id, summary, root_cause, fix_summary,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM incident_embeddings
                WHERE client_id = $2               ← hard filter (B-tree index)
                ORDER BY embedding <=> $1::vector  ← HNSW similarity search
                LIMIT 5;
              returns: [{jira_id, summary, root_cause, fix_summary, similarity}, ...]
        │
        └── results stored in Case.history_matches
              Case persisted to CaseStore
```

### What the subagent sees

The top-5 results from `history_search` are injected into the C4 subagent's prompt as plain text:

```
Similar past incidents (from history_search):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. WH-1234 (similarity: 0.91)
   Context: Order stuck at prioritized in WES.
   Root Cause: Consumer ack dropped due to timeout.
   Resolution: Restarted consumer and re-emitted release.
   Watch Out For: Repeat during high-throughput periods.

2. WH-5678 (similarity: 0.84)
   Context: Order stuck at released, picking engine unresponsive.
   Root Cause: WES–picking engine TCP socket stale after overnight restart.
   Resolution: Bounced picking engine service to reset socket.
   Watch Out For: Check socket liveness before assuming consumer fault.
...
```

Claude reads this as context. It does not know where it came from. From Claude's perspective, it has been handed a list of relevant case notes.

### Fallback when embed_fn is None (PoC mode)

In the PoC, `embed_fn` is not wired. The adapter detects `self._embed_fn is None` and falls back to PostgreSQL full-text ILIKE search:

```python
# vector_adapter.py — search()
if self._embed_fn is not None:
    return await self._vector_search(conn, query, top_k, where_sql, args)
return await self._fulltext_search(conn, query, top_k, where_sql, args)
```

ILIKE search returns `0.5` as a fixed similarity score (no ranking) and matches on substring in `summary`, `root_cause`, or `fix_summary`. Quality is lower than vector search but functional for prototyping.

At write time: `embedding` column is stored as NULL. When a real `embed_fn` is injected, existing NULL rows are not retroactively embedded — you would need to run a backfill job. Plan for this before production.

### Complete pipeline (one diagram)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ BACKGROUND PREP (C7 — when incident is assigned)                        │
│                                                                         │
│  case.description ──→ embed_fn() ──→ Voyage AI  ──→ query_vector       │
│                                                         │               │
│  pgvector: WHERE client_id = ACME                       │               │
│            ORDER BY embedding <=> query_vector          │               │
│            LIMIT 5                          ←───────────┘               │
│                │                                                        │
│                └── top-5 text results → injected into C4 prompt        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ DIAGNOSIS LOOP (C4 — domain subagent)                                   │
│                                                                         │
│  Subagent reads history context + runs db_state_read / log_read tools  │
│  → produces Diagnosis JSON (stuck_transition, root_cause, proposed_fix) │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ HUMAN GATE (/validate → /approve)                                       │
│                                                                         │
│  Engineer reviews proposed fix in Teams DM                              │
│  /approve triggers _write_resolution()                                  │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ WRITE-BACK (after /approve)                                             │
│                                                                         │
│  1. Jira write (system of record)                                       │
│                                                                         │
│  2. Haiku C9: diagnosis text ──→ structured 4-line memory block        │
│                                                                         │
│  3. embed_fn(memory block) ──→ Voyage AI ──→ 1024-float vector         │
│                                                                         │
│  4. pgvector upsert ON CONFLICT (jira_id) DO UPDATE                    │
│     → corpus enriched; next similar incident benefits immediately       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 12. Decisions Made

All decisions locked in. Do not re-derive.

| Decision | Choice | Rationale |
|---|---|---|
| **Vector DB engine** | pgvector (Postgres extension) | Already on Postgres; ACID; relational + vector in one query; operational simplicity |
| **Embedding model** | Voyage AI `voyage-3` | Anthropic-recommended for Claude; strong on technical text; $0.06/1M tokens (cheapest among quality options) |
| **Vector dimension** | 1024 | Fixed by voyage-3's architecture |
| **Schema column** | `vector(1024)` | Matches voyage-3 output |
| **Index algorithm** | HNSW (not IVFFlat) | Better recall, millisecond search, industry standard |
| **HNSW `m`** | 16 | Standard starting point for 1024-dim: balanced recall and memory |
| **HNSW `ef_construction`** | 64 | Reliable recall without expensive index build time |
| **Distance metric** | Cosine (`<=>`) | Standard for text embeddings; angle not magnitude |
| **Upsert key** | `jira_id` | Globally unique; re-resolving same ticket overwrites row |
| **Write-back behavior** | Non-fatal (try/except, warning only) | Jira is the system of record; vector failure is degraded mode, not abort |
| **Memory formulation** | Haiku C9, four-line structured block | Consistent structure embeds and retrieves better than raw prose |
| **Client isolation** | SQL `WHERE client_id = $client` | Hard filter before vector search; relational SQL, not a vector dimension |
| **embed_fn wiring** | Injected as a callable; `None` → ILIKE fallback | Decouples adapter from provider; PoC works without an API key |
| **When to embed at write** | `summary + root_cause + fix_summary` concatenated | The structured summary drives embedding quality; raw fields retained for display |

**Cost summary (production estimate):**

| Line item | Charge | Rate | ~Monthly |
|---|---|---|---|
| Voyage AI (write-back embed) | ~150 tokens × 50 resolutions/day | $0.06/1M | ~$0.014 |
| Voyage AI (search embed) | ~80 tokens × 200 searches/day | $0.06/1M | ~$0.029 |
| Haiku C9 (memory formulation) | ~350 tokens × 50 resolutions/day | $1+$5/1M | ~$1.43 |
| pgvector storage + search | runs on your Postgres | — | $0.00 |
| **Total** | | | **~$1.47/month** |

Embedding and memory formulation are the cheapest line items in the entire system. LLM diagnosis (Sonnet/Opus for C4/C5) dominates the cost budget.
