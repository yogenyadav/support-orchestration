# Support Orchestration

An agentic production-support system for warehouse automation clients. It watches Jira for incidents, diagnoses them through a chain of LLM-powered subagents, and recommends verified fixes to a human support engineer — **who then applies them**.

> **Core invariant:** Agents read, reason, and recommend. Humans act. There is no write tool anywhere in this codebase that touches a client system.

---

## What it does

1. A **Watcher** polls Jira. When an incident's `Assigned To` field is populated, it spawns an **Orchestrator** instance for that incident (≤10 concurrent).
2. The **Orchestrator** triages the incident against a lifecycle-to-domain map (no LLM) and routes to the correct **domain subagent**.
3. The **domain subagent** diagnoses the stuck entity using read-only tools (DB state, logs, GitHub, history) and proposes a specific SQL fix.
4. The engineer receives a `/validate` request in Teams, reviews the diagnosis, and applies the fix manually.
5. The resolution is written to Jira as the record of action.

---

## Architecture

```
Jira ──► Watcher ──► Orchestrator ──► Domain Subagent
                          │                  │
                          │          read-only tools
                          │         (MCP server layer)
                          ▼
                    Teams DM ◄──► Engineer (applies fix)
                          │
                          ▼
                        Jira (resolution record)
```

**Domain subagents** (in priority order by incident volume):

| Key | Domain |
|---|---|
| `WES` | Warehouse Execution System / Orchestration Engine |
| `GTP_PICKING` | GTP Picking station |
| `GTP_DECANT` | GTP Decant station |
| `IMS` | Inventory Management System |
| `ASRS` | Automated Storage and Retrieval |
| `LPN` | Label / Printer (License Plate Numbers) |
| `WCS` | Warehouse Control System |
| `infra` | Infrastructure (VM/vSphere/OS) |
| `ESB` | Enterprise Service Bus (ActiveMQ + Camel) |

---

## Tech stack

- **Python 3.10+**
- **Claude Agent SDK** (`claude-agent-sdk`) — tool loops and subagent orchestration
- **Anthropic Messages API** — single-shot reasoning calls
- **FastMCP** (`mcp>=1.0.0`) — in-process MCP server for read-only tools
- **Models** (route deliberately — output costs 5× input):
  - `claude-haiku-4-5` — classify, route, parse
  - `claude-sonnet-4-6` — diagnose, synthesize
  - `claude-opus-4-8` — novel diagnosis, final fix determination
- **Pydantic v2** — data models (`Case`, `Diagnosis`)
- **SQLite / Postgres** — state store (Case objects, per-client partitioned)
- **pgvector** — vector store for Jira history + Confluence embeddings
- **Azure Bot Framework** — Teams DM transport

---

## Project layout

```
support_orchestration/
  config/         # SLAs, domain registry, base config
  glue/           # Jira client, Teams bot, dialect interpreter
  models/         # Case and Diagnosis data models
  orchestrator/   # Orchestrator: triage, routing, human gates
  storage/        # State store (SQLite/Postgres) + audit store
  subagents/      # Base subagent + per-domain prompts (9 domains)
  tools/          # MCP server + read-only tool implementations
    adapters/     # Production DB/log/vector/HTTP adapters
  watcher/        # Jira poller, intake, background prep (Batch API)

maps/
  base/           # order.yaml + tote.yaml lifecycle state machines

evals/
  fixtures/       # 18 eval fixtures (synthetic + anonymised Jira)
  harness.py      # Triage + diagnosis scoring
  cli.py          # CLI entry point

skills/           # SKILL.md reusable diagnostic procedures (17 skills)
docs/             # Full design specs (1–4)
clients/          # Per-client overlay configs
```

---

## Guardrails (structural, not prompt-only)

- **No write tools.** All DB/log/code/history tools are read-only by credential. The only permitted write is the Jira resolution record (the support system of record, not a client system).
- **Per-client isolation.** Tools and credentials are bound to `case.client`. `PreToolUse` hooks block cross-client reads at the SDK harness layer.
- **Turn cap.** Every agent has `max_turns`. Bounded give-up + SLA-aware termination — no open loops.
- **Audit log.** `PostToolUse` hooks write every tool read to the audit store.
- **Human gate.** No fix proposal is surfaced before `Assigned To` is populated. The fix is always applied by a human.

---

## Setup

```bash
# 1. Create venv and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure secrets
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and any integrations you need

# 3. Run tests
pytest

# 4. Run the eval harness
python -m evals                         # triage only — no API key needed
python -m evals --validate-fixtures     # adapter wiring check — no LLM
python -m evals --diagnose              # LLM diagnosis (needs ANTHROPIC_API_KEY)
python -m evals --verbose               # any of the above + logs/evals.log
```

---

## Connectivity tiers

Each client has one of three connectivity postures (stored in Phoenix):

| Tier | Access |
|---|---|
| `direct_connect` | DB + logs readable after a human opens the session |
| `s3_log` | Logs only, via an AWS S3 `{client-name}` bucket, gated by one-time human confirmation |
| `human_relay` | No prod access; agent asks precise questions, human relays answers |

---

## Eval status

18 fixtures across all 9 domains. 100% triage accuracy (deterministic, no LLM). Diagnosis eval active with `--diagnose`.

| Domain | Fixtures |
|---|---|
| WES | 5 |
| ESB | 2 |
| GTP_PICKING | 2 |
| IMS | 2 |
| WCS | 2 |
| ASRS | 2 |
| LPN | 2 |
| infra | 1 |
| GTP_DECANT | 1 |

---

## Docs

Full design specifications in `docs/`:

- `docs/1-warehouse-systems.md` — systems being supported
- `docs/2-support-process.md` — support workflow, connectivity tiers, SLAs
- `docs/3-agent-design.md` — orchestrator + subagents, guardrails, human interaction
- `docs/4-technical-build.md` — language, model calls, context engineering, infra, CI/CD

---

## Status

All 10 build prompts complete. 218 unit tests passing.

| Component | Status |
|---|---|
| Lifecycle maps (order + tote) | Done |
| Read-only tool layer (MCP) | Done |
| Eval harness | Done |
| Watcher + intake + state store | Done |
| Orchestrator | Done |
| WES subagent + Teams dialect | Done |
| Teams bot + production adapters + Jira write | Done |
| All 9 domain subagents | Done |
| Mocked tool responses + diagnosis eval | Done |
