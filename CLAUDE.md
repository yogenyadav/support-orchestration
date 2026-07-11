# CLAUDE.md

Project orientation for Claude Code. Read this first, then `docs/` before starting any task.

---

## What we're building

A **long-running agentic production-support system** for a warehouse-automation systems integrator (125+ client warehouses, on-prem deployments). It watches Jira for incidents, diagnoses them, and recommends verified fixes to a human support engineer.

**North star — non-negotiable:** *Agents read, reason, and recommend. Humans act.* The agent **never writes** to any client system. It diagnoses through to a verified, determined fix, and a human applies it. There is **no write tool** anywhere in this codebase.

## The design is already decided — read it, don't re-derive it

Full specs live in `docs/` (GitHub markdown + Mermaid diagrams):

- `docs/1-warehouse-systems.md` — the operation and software systems being supported
- `docs/2-support-process.md` — the support workflow, connectivity tiers, SLAs
- `docs/3-agent-design.md` — orchestrator + subagents, guardrails, human interaction
- `docs/4-technical-build.md` — language, model calls, context engineering, infra, CI/CD

When a task touches architecture, **consult these docs as the source of truth.** If something here conflicts with a doc, ask before diverging.

## Architecture in one paragraph

A deterministic **Watcher** (no LLM) polls Jira; when an incident's `assigned to` field is populated, it spawns **one Orchestrator instance per incident** (≤10 concurrent). The orchestrator triages against a cached **lifecycle-to-domain map** and routes to **domain subagents** (WES, GTP Picking, GTP Decant, IMS, ASRS, LPN, WCS, infra, ESB — in priority order by incident volume) that diagnose the **stuck-entity state machine** using a **read-only tool layer**. All context flows through one accumulating **Case object**. The agent determines and verifies a fix; a human applies it.

## Key invariants (enforce these in code)

1. **No write tool exists.** All DB/log/code/history tools are read-only by credential. Never add a tool that mutates a client system.
2. **Per-client isolation.** Tools and credentials are bound to `case.client`. An agent working one client must never reach another's data.
3. **Human gate before action.** No Teams DM, no Direct Connect session, no fix proposal before `assigned to` is populated. The fix is always applied by a human.
4. **Guardrails are structural, not prompt-only.** Use Agent SDK `PreToolUse` hooks to block writes, enforce client scope, enforce the tool allowlist, and cap `max_turns`. Use `PostToolUse` to audit every read.
5. **Bounded loops.** Every agent has `max_turns` and SLA-aware termination (diagnose / escalate / reroute / boundary / bounded-give-up). Never an open loop.

## Tech stack

- **Language:** Python (3.10+).
- **Agents:** Claude **Agent SDK** (`pip install claude-agent-sdk`). Use `query()` for tool-loop work; raw Messages API for single-shot reasoning.
- **Models (pin exact strings; re-verify at build time):** `claude-haiku-4-5` (classify/route/parse), `claude-sonnet-4-6` (diagnose/synthesize), `claude-opus-4-8` (novel diagnosis + final fix). Output costs 5× input — route deliberately.
- **Custom tools:** in-process MCP servers via **FastMCP** (`pip install mcp>=1.0.0`). Server: `FastMCP("support")`; tool names are `mcp__support__<bare_name>`. See `tools/mcp_server.py`.
- **Context economy:** prompt caching on the stable system prompt + lifecycle map; Batch API for background prep; retrieve (vector store) instead of stuffing; structured JSON outputs.

## MCP servers (see docs/4 §4.8)

- **Off-the-shelf (configure):** Atlassian (Jira+Confluence), GitHub, AWS, Salesforce. All read-only against client systems. The **only** allowed write is the Jira resolution record (Jira is the support system of record, not a client system).
- **Custom (build, read-only):** `db-state-reader`, `phoenix-resolver`, `log-reader` (direct/S3/human-relay router), `history-retrieval` (vector search), `teams-dialect`.
- Read-only + per-client scope are enforced **at the MCP layer**, not by prompt. Direct Connect db/log servers run on the per-client access runner and only after a human opens the session.

## Domain subagents (priority order — highest incident volume first)

| Domain key | Full name | Notes |
|---|---|---|
| `WES` | Orchestration Engine | **Pages most — first subagent built** |
| `GTP_PICKING` | GTP Picking | Good-to-Pick station picking |
| `GTP_DECANT` | GTP Decant | Good-to-Pick station decant |
| `IMS` | Inventory Management | Check count-hold FIRST before diagnosing |
| `ASRS` | Automated Storage and Retrieval | Replaces "AutoStore/Knapp" in all code |
| `LPN` | Label/Printer | License Plate Number printing |
| `WCS` | Warehouse Control System | **Lowest incident priority** |
| `infra` | Infrastructure | Cross-cutting; VM/hypervisor/OS |
| `ESB` | Enterprise Service Bus | ActiveMQ + Apache Camel |

## Storage (see docs/4 §4.6.1) — DECIDED, do not re-derive

Three durable stores, all owned by the support system (no client production data):

| Store | Engine (prototype) | Purpose |
|---|---|---|
| **State store** | Postgres (or SQLite single-node) | Case objects partitioned by client; crash-recovery via rehydrate+resume |
| **Vector store** | pgvector (same Postgres instance) | Jira history + Confluence embeddings; powers history-match deflection |
| **Audit store** | SQLite for PoC → Postgres in Prompt 5 | Every agent read + every human approval; compliance-grade. `storage/audit.py` → `AuditStore`. |
| **Eval fixtures** | Files in repo (`evals/fixtures/`) | Past resolved incidents for the replay harness |

**Rules locked in:**
- Partition state store by `client` — per-client isolation at the storage layer, not just the tool layer.
- `pgvector` combines state + audit + vector into one Postgres engine for the prototype.
- Client production data is **never stored** — read live, reasoned over in-flight, discarded.
- Audit data is long-lived (compliance); Case objects age out after resolution; vector corpus grows continuously.
- **Agentic RAG write-back:** on every resolution, `Orchestrator._write_resolution()` calls `VectorStoreAdapter.write()` after the Jira write. Non-fatal — vector failure logs a warning but never aborts resolution. Jira is still the system of record.
- **Vector store schema:** HNSW index `WITH (m=16, ef_construction=64)` on the embedding column; `UNIQUE(jira_id)` constraint for idempotent upsert (re-resolving the same ticket overwrites the row, not duplicates). When `embed_fn=None`, write stores NULL and search falls back to ILIKE.

## Skills (see docs/4 §4.9)

Skills = `SKILL.md` folders of reusable procedural know-how the agent loads on demand. Keep prompts/CLAUDE.md lean; offload verbose procedures to skills. Skill ≠ subagent (a subagent has its own context; a skill is knowledge any agent loads). Author, in priority order: `diagnostic-method`, `lifecycle-map-reading`, `dialect-protocol`, `evidence-gathering`, per-domain diagnosis skills (`wes-`, `gtp-picking-`, `gtp-decant-`, `ims-` [check count-hold first], `asrs-`, `lpn-`, `wcs-`, `infra-`, `esb-`), `fix-determination`, `jira-record`. Store under `skills/`.

## Harness engineering (see docs/4 §4.10)

The Agent SDK is the harness. Anything that must be true **every time** — no writes, per-client isolation, `max_turns`, audit — lives in the **harness (hooks + config)**, not the prompt. `PreToolUse` hooks enforce write-block / client-scope / allowlist / turn-cap; `PostToolUse` audits every read. Prompts guide; the harness enforces.

## Connectivity reality (per client, from Phoenix)

- Universal read: GitHub (base + client org), Jira, Confluence, Salesforce.
- `direct_connect` clients: DB/logs/apps readable **after a human opens the session**.
- `human_relay` clients: **no** prod access; agent asks the engineer precise questions, human relays.
- S3-log clients (few): logs in an AWS S3 `{client-name}` bucket via AWS MCP, gated by a one-time human confirmation.

**Phoenix** = internal web pages (one per client) listing IP addresses, usernames, and passwords to connect to each client's infrastructure. For PoC: connection details supplied as a fixture via `register_poc_fixture(client_id, {...})` in `support_orchestration/tools/phoenix_resolver.py`. Production: implement a scraper or REST adapter against the Phoenix pages.

## Agent ↔ human interaction

- Read the assignee from Jira → open a **Teams DM** with that engineer.
- Live dialogue in the DM; durable outcomes (**approvals + final diagnosis**) written to **Jira** as the record.
- **Dialect:** agent → human always prefixed (`/info`, `/ask`, `/validate`, `/approve`, `/status`), **one open request at a time**. Human → agent: free-form natural language accepted as-is; no syntax required. `/approve` outcomes mirror to Jira.

## Build order

| Step | Status | Notes |
|---|---|---|
| 1. Lifecycle-to-domain maps (`order` + `tote`) | ✅ DONE | `maps/base/order.yaml` (13 states, 13 transitions) + `maps/base/tote.yaml` (11 states, 10 transitions). All transitions marked [INFERRED] — engineers must review and ground in real code/schema. |
| 2. Read-only tool layer | ✅ DONE | `tools/mcp_server.py`: all 5 tools registered (`db_state_read`, `log_read`, `github_read`, `history_search`, `phoenix_resolve`). PreToolUse hooks + AuditStore wired. 160 unit tests pass (as of Prompt 7). |
| 3. Eval harness | ✅ DONE | Triage scoring is live. `python -m evals [--domain WES]`. 4 WES fixtures. Diagnosis scoring active: pass `anthropic_client=` to `run_all_evals()`. |
| 4a. Watcher + intake contract + Case state store | ✅ DONE | `watcher/jira_poller.py` (full poll loop), `watcher/intake.py`, `storage/state_store.py` (SQLite CaseStore), `watcher/background_prep.py` (C7 Batch API), `glue/jira.py` (JiraClient interface + AtlassianJiraClient). See `docs/4 §4.7 As-built (Prompt 5)`. |
| 4b. Orchestrator | ✅ DONE | Prompt 6. Full `Orchestrator` class: C1→C3 triage, escalate-vs-probe routing, reroute guard, `/validate`+`/approve` human gates, state store checkpoints. 36 tests. |
| 4c. First domain subagent (WES) + dialect | ✅ DONE | Prompt 7. `subagents/base.py` — `diagnose()` implemented (raw Messages API tool loop, hooks, relay sentinel, bounded give-up). `subagents/prompts.py` (NEW). `glue/teams.py` — `c6_interpret_reply()` (C6 Haiku). Orchestrator default factory wires deps. 160 tests. |
| 5. Teams bot + MCP adapters + Jira write | ✅ DONE | Prompt 8. `glue/bot.py` — `BotFrameworkTransport` (Azure Bot REST API, asyncio.Queue, token cache). `tools/adapters/` — 5 production adapters: `OracleDbAdapter`, `PostgresDbAdapter`, `MsSqlDbAdapter`, `PgvectorStoreAdapter`, `SshLogAdapter`, `HttpPhoenixAdapter`, `GithubApiAdapter`. `glue/jira.write_resolution()` implemented + wired into orchestrator `_write_resolution()`. 218 tests. |
| 6. Additional domain subagents | ✅ DONE | Prompt 9. All 8 remaining domain subagents fully built (WES pattern): rich `{DOMAIN}_DOMAIN_CONTEXT` + builder functions in `subagents/prompts.py`; `system_prompt` properties updated in `subagents/base.py`; 14 new eval fixtures (18 total, 100% triage accuracy). |
| 7. `mocked_tool_responses` + diagnosis eval | ✅ DONE | Prompt 10. `mocked_tool_responses` added to all 18 fixtures (phoenix_resolve, db_state_read, history_search, log_read). Fixed `FixtureDbAdapter.query()` bug. Added `--diagnose` CLI flag. Run: `ANTHROPIC_API_KEY=... python -m evals --diagnose`. 218 tests pass. |
| 8. Agentic RAG write-back | ✅ DONE | Post-P10 (2026-07-11). `VectorStoreAdapter.write()` (upsert on `UNIQUE(jira_id)`, HNSW index `m=16/ef=64`). `Orchestrator._formulate_memory()` (Haiku C9 — structured 4-line block: **Context / Root Cause / Resolution / Watch Out For**). Write-back triggered on every resolution; non-fatal. `vector_adapter` kwarg on `Orchestrator.__init__`. 225 tests. |

## Eval harness — what's running

Run: `.venv/bin/python3.12 -m evals [--domain WES]` from repo root. Exit code 0 = all triage checks pass; 1 = regression.

**Fixture schema** (canonical — all fixtures in `evals/fixtures/` must conform):
```yaml
fixture_id: str
grounding: "[SYNTHETIC]" | "[JIRA:WH-XXXX anonymized]"
input:
  jira_ticket_id, client, priority (P1–P4), description,
  entity_type (order|tote), entity_id, entity_current_state
ground_truth:
  owning_domain, stuck_transition ("from → to"), root_cause,
  blocker_class, fix_applied, fix_sql (nullable)
scoring:
  triage_correct_if:    {owning_domain}
  diagnosis_correct_if: {stuck_transition, blocker_class}
  fix_match_if:         {proposed_fix_must_mention: [keywords]}
mocked_tool_responses:  # optional — enables diagnosis eval in Prompt 7
  db_state_read / log_read / history_search / github_read / phoenix_resolve
```

**Current fixtures** (18 total, 100% triage accuracy — added in Prompt 9):
- WES (5): `example_order_stuck_prioritized_01`, `wes_order_lost_ack_01`, `wes_order_ims_hold_01`, `wes_order_stuck_at_validated_01`
- ESB (2): `esb_order_stuck_queue_01`, `esb_order_bad_input_01`
- GTP_PICKING (2): `gtp_picking_order_service_down_01`, `gtp_picking_order_ims_hold_01`
- IMS (2): `ims_order_count_discrepancy_01`, `ims_order_service_down_01`
- WCS (2): `wcs_order_socket_failure_01`, `wcs_order_service_down_01`
- ASRS (2): `asrs_order_storage_unavailable_01`, `asrs_order_retrieval_timeout_01`
- LPN (2): `lpn_order_printer_fault_01`, `lpn_order_data_missing_01`
- infra (1): `infra_order_oom_crash_01`
- GTP_DECANT (1): `gtp_decant_bin_not_placed_01`

Note: IMS, ASRS, LPN, infra, GTP_DECANT are reroute-target domains — their fixtures use a parent-domain `entity_current_state` (GTP_PICKING or WCS) for triage, and test diagnosis-layer rerouting.

**Scoring implementation** (`evals/harness.py`):
- `triage_accuracy` — live: `load_lifecycle_map` + `find_transition` → `owning_domain` (no LLM)
- `diagnosis_correct` + `fix_match` — active when `anthropic_client=` passed to `run_all_evals()`. Without client, diagnosis is skipped (NotImplementedError → `diagnosis_skipped=True`).
- **Prompt 10 done:** All 18 fixtures have `mocked_tool_responses` (phoenix_resolve + db_state_read + history_search + log_read). `FixtureDbAdapter.query()` bug fixed (now matches by entity_id only). Adapter wiring validation (`--validate-fixtures`) and `--diagnose` CLI flags added. `--verbose` writes DEBUG logs to `logs/evals.log`.

**CLI quick-reference:**
```bash
.venv/bin/python3.12 -m evals                           # triage — instant, no API key
.venv/bin/python3.12 -m evals --validate-fixtures       # adapter wiring — no LLM
.venv/bin/python3.12 -m evals --diagnose                # LLM diagnosis (needs ANTHROPIC_API_KEY)
.venv/bin/python3.12 -m evals --verbose                 # any of the above + logs/evals.log
```

## How to work in this repo

- Use **plan mode** for scaffolding tasks before writing code.
- Keep guardrails in from day one, even in the prototype — don't retrofit safety.
- This repo *builds* the system; the system itself is a separate long-running runtime. Claude Code is the build environment, not the production runtime.
- Mirror the product's own structure: a **base** package + **per-client overlay** config (lifecycle deltas, access tier, log posture, schema specifics).

## Open ground-truth items (ask the human; don't invent)

**Resolved — do not re-derive:**
- **DB schemas:** never pre-provided; vary per client. Discovered at runtime (GitHub code → `information_schema` → `/ask` engineer). Stub adapters for PoC. Schema learning loop (save to state store per-client) is a future iteration.
- **Jira ticket fields:** `Assigned To` (trigger), `Priority` (P1–P4), `Created` (SLA clock), `Summary` (optional/incomplete), `Background` (optional/incomplete), `Linked Issues` (mine when present). Agent fills gaps via `/ask` + vector-store history. See `docs/2-support-process.md §2.2`.
- **SLA targets:** P1=4h · P2=8h · P3=72h · P4=168h (7 days). Already in `config/base.py`.
- **Corrective-action catalog:** dominant fix is a targeted DB `UPDATE` to move a stuck entity to terminal/restart state. Agent produces table+row, target state, exact SQL, and a verification step. Human applies.
- **Hardware-vs-software discrimination:** hypervisor is the boundary — physical mechanics → field engineer; VM/vSphere → infra subagent; Windows service/MS SQL → WCS subagent.

**Still open:**
1. **SLA attainment baseline** — actual before-agent percentages from a Jira query on resolved incidents. Non-blocking for PoC.
2. **Base code paths + one client overlay** — for grounding lifecycle map [INFERRED] transitions. Non-blocking for Prompt 8.

When you hit an open item, surface it explicitly rather than guessing.
