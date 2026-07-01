# Claude Code — Kickoff Prompts

Paste these into Claude Code in order. Each assumes `CLAUDE.md` and `docs/1..4` are in the repo. Run the first few in **plan mode** (review the plan before it writes).

---

## 0 · Orientation (run once, first session)

```
Read CLAUDE.md and everything in docs/. Summarize back to me, in your own words:
(1) the north-star constraint, (2) the architecture, (3) the build order, and
(4) the open ground-truth items you'll need from me. Don't write any code yet —
I want to confirm you've internalized the design before we start.
```

---

## 1 · Repo scaffolding (plan mode)

```
Propose a repository structure for this project based on docs/4-technical-build.md
and the build order in CLAUDE.md. I want a Python project using the Claude Agent SDK.

Requirements:
- a base package + per-client overlay config layout (mirrors our product's base+client-org model)
- clear separation: watcher / orchestrator / subagents / tools (read-only) / evals / glue (jira, teams)
- a place for the lifecycle-to-domain maps (base + per-client deltas)
- pyproject.toml with claude-agent-sdk pinned, plus dev deps (pytest, ruff, etc.)
- no write tool anywhere; read-only tool layer only

Show me the tree and the rationale first. Don't create files until I approve the plan.
```

---

## 2 · Lifecycle-to-domain map for `order` and `tote` — START HERE

> This is the system's brain. It's also the task where I'll need to point you at real code/schemas.

```
We're building the lifecycle-to-domain map described in docs/3-agent-design.md §3.3.
Start with the `order` entity, then `tote`.

For each entity, produce a structured map (YAML or JSON — propose which) where every
state transition records: owning domain, expected trigger, candidate blockers,
whether IMS can halt it, and dependency edges to other entities.

I'll point you at the schema and the base code so you can extract real states and
transitions — ask me for the paths. Draft the map from:
  (1) DB schemas → the actual states,
  (2) base code (and one client overlay) → transitions and triggers,
  (3) past Jira incidents → the real-world blockers that actually occur.

Mark anything you're inferring vs. anything grounded in code, so my engineers can
correct it. This is a draft for human review, not ground truth.
```

---

## 3 · Read-only tool layer (plan mode)

```
Implement the shared read-only tool layer from docs/4-technical-build.md §4.3 and §4.6,
as in-process MCP tools (create_sdk_mcp_server + @tool).

Tools: db_state_read, log_read (direct / S3-via-AWS-MCP / human-relay variants),
github_read (base + client org), history_search (vector retrieval over Jira/Confluence),
phoenix_resolve (per-client access tier + log posture, cached).

Hard requirements:
- every tool is READ-ONLY. Do not implement any write/mutate capability.
- every tool is client-scoped: it receives case.client and must refuse cross-client access.
- add PreToolUse hooks: block_writes, enforce_client_scope, enforce_allowlist, enforce max_turns.
- add a PostToolUse audit hook that logs every read (what / when / client / credential).

Plan the interfaces and the hook design first. Stub the external connections (DB, S3,
Phoenix) behind clean adapters so we can run against recorded fixtures in tests.
```

---

## 4 · Eval harness over past incidents (plan mode)

```
Build the eval-driven test harness from docs/4-technical-build.md §4.7.

Goal: replay past resolved Jira incidents (known root cause + fix) in shadow mode and score:
  - triage accuracy (did it route to the correct domain?)
  - diagnosis correctness (did it find the right stuck transition / blocker?)
  - fix-match (does the proposed fix match what the engineer actually did?)

I'll provide a set of anonymized past incidents as fixtures — design the fixture format
and ask me for them. The harness must run in CI, block merges on regression, and produce
a per-domain scorecard. No live systems — fixtures only.
```

---

## 5 · Watcher + intake contract (plan mode)

```
Implement the Watcher from docs/3-agent-design.md §3.2.

Behavior:
- poll Jira for new incidents (deterministic, no LLM).
- on a new incident: begin read-only background prep in parallel (history match, Confluence
  pull, lifecycle locate, client access tier, log posture). NO human-facing action yet.
- watch the `assigned to` field (NOT case owner). Only when populated → spawn an orchestrator
  for that incident and open the human dialogue.
- track `assigned to` for reassignment; follow it to a new engineer, carrying the Case object.

Define the Case object schema first (per docs/3 §3.5), persisted to the state store.
```

---

## 6 · Orchestrator (plan mode)

```
Implement the per-incident orchestrator from docs/3-agent-design.md §3.1, §3.4.

It must: ingest the Case object, assess priority/SLA, identify the stuck entity and its
position on the lifecycle map, match history, triage to the owning domain, and route to a
domain subagent. Implement the escalate-vs-probe rule from §3.4. Use model routing per
docs/4 §4.2 (Haiku for classification, Sonnet for triage). Cache the system prompt + base
lifecycle map per docs/4 §4.3–4.4. Re-route on a subagent bounce-back.

Don't build all six subagents yet — wire one (the domain that pages most; ask me which).
```

---

## 7 · First domain subagent + dialect (plan mode)

```
Implement one domain subagent (the one we chose) plus the Teams dialect from docs/3 §3.7.

Subagent: the state-machine diagnosis loop (find entity → stuck transition → blocker) using
the read-only tools, returning the structured diagnosis/fix JSON from docs/4 §4.3.

Dialect: agent→human prefixed verbs (/info /ask /validate /approve /status), ONE open request
at a time; human→agent free-form accepted as-is. /approve mirrors to Jira. For now, stub the
Teams transport behind an interface so we can test the dialect logic without a live bot.
```

---

## 8 · MCP servers (configure + build)

```
Set up the MCP layer per docs/4-technical-build.md §4.8.

Off-the-shelf servers to configure (claude mcp add): Atlassian (Jira+Confluence),
GitHub, AWS, Salesforce. Use READ-ONLY credentials against client systems. The ONLY
allowed write is the Jira resolution record — Jira is the support system of record,
not a client production system. Make that boundary explicit in the wiring.

Custom in-process MCP servers to build (read-only, client-scoped):
db-state-reader, phoenix-resolver, log-reader (routes direct / S3-via-AWS / human-relay),
history-retrieval (vector search), teams-dialect. Enforce read-only and per-client scope
at the MCP layer — not by prompt. Stub external connections behind adapters for tests.
```

---

## 9 · Author the skills (after the lifecycle map exists)

```
Create the Agent Skills described in docs/4-technical-build.md §4.9, under a skills/ dir,
each as a SKILL.md (plus scripts if needed). Build in this order:

1. diagnostic-method — the universal find-entity → stuck-transition → blocker method.
2. lifecycle-map-reading — use the map to locate the stall and pull the blocker checklist.
3. dialect-protocol — one open request at a time; verbs; /approve mirrors to Jira.
4. evidence-gathering — direct probe vs precise human-relay questions vs S3 log reads.
5. per-domain diagnosis skills (wes-, wcs-, ims- [check count-hold FIRST], esb-, picking-,
   decant-, infra-) — each encodes that domain's failure modes, what to read, blocker checklist.
6. fix-determination — produce + self-verify a fix (reversible? verification step?); human applies.
7. jira-record — structured resolution write that enriches history.

Keep each skill focused; offload verbose procedure here so system prompts stay lean.
A skill is shared knowledge — distinct from a subagent (which has its own context).
```

---

## Tips while building

- Prefer **plan mode** for anything that creates many files or touches architecture.
- Whenever Claude Code wants to assume a schema, a ticket field, or a corrective action — make it **ask you** (these are the open ground-truth items in `CLAUDE.md`).
- Keep the **read-only / no-write** invariant sacred. If a prompt ever seems to lead toward a write capability, stop and reconsider — the human applies the fix.
- Commit the lifecycle maps and prompts as **reviewed artifacts** — a map change shifts routing for many incidents.
