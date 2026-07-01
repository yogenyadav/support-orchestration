# 3 · Agent Design — Orchestrator, Subagents, Human Guardrails & Interactions

The design of the long-running orchestrator and domain subagents, how work hands off, where humans sit as guardrails, and exactly how agents and humans interact.

**North star:** *Agents read, reason, and recommend. Humans act.* The agent never writes to a client system; it diagnoses through to a **verified, determined fix**, and a human applies it.

---

## 3.1 Topology — one orchestrator design, one instance per incident

```mermaid
flowchart TD
    W[WATCHER · deterministic, no LLM<br/>polls Jira; spawns orchestrators] -->|1 per incident| O1
    W -->|1 per incident| O2
    W -->|1 per incident| ON

    subgraph "≤ 10 concurrent incidents"
      O1[Orchestrator · incident A]
      O2[Orchestrator · incident B]
      ON[Orchestrator · incident N]
    end

    O1 --> SUB
    subgraph "Domain Subagents (specialists)"
      SUB[decant · WES/orchestration · picking ·<br/>WCS/controls · IMS/inventory · infra]
    end

    SUB --> TOOLS
    subgraph "Shared READ-ONLY tool layer"
      TOOLS[jira · confluence · github base+client ·<br/>history retrieval · phoenix-resolver ·<br/>db-state-reader · log-reader · teams-dialect]
    end

    style W fill:#fef7e0
    style TOOLS fill:#e6f4ea
```

- **One orchestrator *design*; one *instance* per active incident** (up to ~10 concurrent). A thin **deterministic Watcher** (not an LLM) polls Jira and spawns them.
- **Orchestrator:** triage + routing + human dialogue + final fix assembly + Jira record. Reasons and routes; does not do deep domain diagnosis itself.
- **Six domain subagents:** mirror the org chart exactly — each owns one domain's deep diagnosis.
- **Shared tool layer:** read-only capabilities every agent uses. **There is no write tool.**

> The human support engineer = the orchestrator. The domain engineers = the subagents. We're encoding the org chart that already exists.

---

## 3.2 Intake contract — watch Jira, wait for the assignee

```mermaid
flowchart TD
    NEW[New Jira incident appears] --> PREP[Begin READ-ONLY background prep<br/>history match · Confluence · lifecycle locate ·<br/>client access tier · log posture]
    PREP --> WAIT{assigned to<br/>populated?}
    WAIT -->|no| PREP
    WAIT -->|yes — manager or self-assign| GO[Spawn orchestrator for THIS engineer]
    GO --> DM[Open Teams DM with assignee<br/>present warm-start dossier]

    style PREP fill:#e6f4ea
    style GO fill:#e8f0fe
```

- Agents **watch only Jira** (Salesforce is upstream; Jira is always created).
- The trigger field is **`assigned to`** (not `case owner`). **No human interaction until it's populated.**
- **But waiting ≠ idle:** while unassigned, agents do **read-only central prep** so the engineer gets a warm start. No Direct Connect session, no human questions, no fix proposals during this phase.
- Track `assigned to` for **reassignment** — follow it to the new engineer, carrying context.

---

## 3.3 The lifecycle-to-domain map — the system's brain

Routing isn't "classify into a domain." It's "**where in the entity's lifecycle did the transition fail?**" — and the owning domain falls out of that.

```mermaid
flowchart LR
    subgraph "Lifecycle map (per entity, base + client deltas)"
      direction TB
      ST[State] --> TR[Transition]
      TR --> OWN[Owning domain]
      TR --> TRIG[Expected trigger]
      TR --> BLK[Candidate blockers]
      TR --> IMSG[IMS halt? cross-cutting]
      DEP[Dependency edges to other entities<br/>order→bins, order→inventory]
    end
```

For each transition the map stores: **owner, trigger, blockers, IMS-halt flag, dependency edges**. Example — `prioritized → released` is **WES**-owned; trigger = WES emits release; blockers = WES backed up / message stuck in ActiveMQ / picking-engine didn't ack / **IMS hold**. Find an order stuck in `prioritized` → the map hands you the domain *and* a ready blocker checklist.

> Built from **code** base+client (transitions/triggers), **Jira history** (real blockers), and Confluence docs. The map encodes **logical** states and transitions — it does not embed physical DB schema details (table names, column names, state string values), because those vary per client and are discovered at runtime. Every resolved incident enriches the blocker-lists and the map compounds over time.

---

## 3.4 How the orchestrator decides the domain

```mermaid
flowchart TD
    T[Incident] --> H[History match<br/>strongest prior — issues recur]
    H --> TXT[Ticket text<br/>seeds candidate domains, NOT the answer<br/>symptom location ≠ root cause]
    TXT --> STATE[Read entity STATE<br/>cheap, decisive: where on lifecycle did it stall?]
    STATE --> MAP[Lifecycle map → owning domain]
    MAP --> CONF{Confidence?}
    CONF -->|one dominates| ROUTE[Route to that subagent]
    CONF -->|ambiguous + cheap decisive probe + clock allows| PROBE[Probe 2-3 candidates in parallel]
    CONF -->|ambiguous, no decisive probe / novel / tight SLA| ESC[Escalate with ranked hypotheses]
    ROUTE --> BACK{Subagent: mine?}
    BACK -->|no| REROUTE[Bounce back → re-route]
    BACK -->|yes| DIAG[Diagnose]
```

### Escalate-vs-probe rule

| Situation | Action |
|---|---|
| One hypothesis dominates | **Route** |
| Ambiguous, a cheap/safe probe would split hypotheses, clock allows | **Probe** (read-only) |
| Ambiguous, no decisive cheap probe | **Escalate** with ranked hypotheses |
| Novel (no history match) | **Escalate** early — human leads, capture for next time |
| Tight SLA | **Escalate now** with best hypotheses so a human starts with runway |

Escalation is **never a shrug** — it hands over ranked hypotheses, evidence, what was checked, and what it would check next.

---

## 3.5 Handoff & context preservation — the Case object

One **structured Case object** travels with the incident and accumulates at every hop; nothing is re-derived.

```mermaid
flowchart LR
    O[Orchestrator] -->|Case obj| WES[WES subagent]
    WES -->|"not mine" + findings appended| O
    O -->|Case obj| IMS[IMS subagent]
    IMS -->|diagnosis + proposed fix| O
    O --> HUMAN[Human gate]

    note[Case carries: ticket+SLA+client tier,<br/>hypothesis+alternatives, lifecycle slice,<br/>evidence, dialogue, trail, proposed_fix, status]
```

Reroute and escalation are first-class; the **trail** means a case can go orchestrator → WES → back → IMS with nothing lost. If a process dies, a new orchestrator **rehydrates from the Case object** and resumes.

---

## 3.6 Guardrails (structural, not just prompts)

```mermaid
flowchart TD
    A[Agent capability] --> R{Read-only?}
    R -->|yes: DB/logs/code/history| OK[Allowed]
    R -->|write / mutate prod| NO[Impossible — no write tool exists]

    G1[PreToolUse hook] --> B1[block writes]
    G1 --> B2[enforce per-client scope]
    G1 --> B3[allowlist only]
    G1 --> B4[enforce max_turns]
    G2[PostToolUse hook] --> AUD[audit every read]

    style NO fill:#fce8e6
    style OK fill:#e6f4ea
```

| Guardrail | Enforcement |
|---|---|
| Agent never writes to client systems | No write tool exists; all data tools read-only by credential |
| No human-facing action before `assigned to` | Watcher gate; only read-only central prep runs while unassigned |
| Direct Connect needs human to open session | DB/log tool returns "needs connection"; agent must `/ask` first |
| Per-client isolation | Credentials + tool scope bound to `case.client`; cross-client impossible |
| Bounded loops | `max_turns` + SLA-aware termination (diagnose / escalate / reroute / boundary / bounded-give-up) |
| Full audit | Every read logged; approvals mirrored to Jira |

---

## 3.7 Agent ↔ Human interactions (what we settled on)

### Channels: Jira = record, Teams DM = live dialogue

```mermaid
flowchart LR
    AGENT[Agent] -->|reads who is assigned| JIRA[(Jira · always · system of record)]
    AGENT -->|opens 1:1 DM with assignee| TEAMS[Teams Direct Message · always available]
    AGENT -->|durable outcomes: approvals + final diagnosis| JIRA

    note[Incident TEAM chat is inconsistent → not used.<br/>Teams DM to the engineer is always available.<br/>Jira is always created → backbone + audit.]
```

- The agent **reads the assignee from Jira**, then **opens a Teams DM** with that one engineer.
- **Live back-and-forth happens in the DM**; **durable outcomes (approvals, final diagnosis) are written to Jira** as the audit record and to enrich history.

### The dialect (starts minimal, evolves from first principles)

```mermaid
sequenceDiagram
    participant AG as Agent
    participant EN as Engineer

    Note over AG: ONE open request at a time
    AG->>EN: /info current status of order 12345 in orders table<br/>(why: still 'prioritized' ⇒ WES release issue; 'released' ⇒ downstream)
    EN->>AG: still prioritized   ← free-form, no syntax required
    AG->>EN: /info is there an IMS count hold on 12345?
    EN->>AG: no hold
    AG->>EN: /validate root cause = lost ack on release; agree?
    EN->>AG: agree
    AG->>EN: /approve recommend re-driving the release (human applies)
    EN->>AG: approved   → mirrored to Jira
```

**Decisions locked:**

- **Direction-encoded, symmetric protocol:** `/<verb> <context>` from agent → human. The machine bears all structure.
- **Agent → human:** always prefixed; verbs (minimal, growing): `/info`, `/ask`, `/question`, `/clarify`, `/validate`, `/approve`, `/status`. `/approve` is distinct from day one because it carries audit + safety machinery and is mirrored to Jira.
- **Human → agent:** **free-form natural language accepted as-is**; the reply character is optional sugar. The human bears **no** syntax burden.
- **One open request at a time** → reply matching is unambiguous (this is what *lets* the human reply naturally).
- **Everything logged** so real incidents evolve the verb set.

### Agent identity on Teams

```mermaid
flowchart TD
    P[Prototype] --> BOT[Azure Bot + Entra app registration<br/>Teams SDK · store conversation references<br/>for proactive DMs]
    BOT --> PROD[Production] --> EID[Entra Agent ID / Agent 365<br/>governed agent identity, RBAC,<br/>auto-suspend on owner offboarding]
    DEP[Dependency: M365 admins must register the bot<br/>+ grant Graph permissions — critical path]
```

- **Prototype:** registered **Azure Bot** (Entra app), proactive DMs via stored conversation references.
- **Production:** migrate to **Entra Agent ID / Agent 365** for governed identity.
- **Hard dependency:** Microsoft 365 admins must register the bot and grant permissions — line this up early.

---

## 3.8 End-to-end agent run (with human gates)

```mermaid
sequenceDiagram
    participant W as Watcher
    participant O as Orchestrator
    participant S as Domain Subagent
    participant E as Engineer (assignee)
    participant J as Jira

    W->>W: new Jira incident → read-only prep (parallel)
    J-->>W: assigned to populated
    W->>O: spawn for this incident (Case object)
    O->>E: Teams DM — warm-start dossier
    O->>O: triage via lifecycle map + history
    O->>S: route (Case object)
    alt Direct Connect client
        S->>E: /ask please open the connection
        E-->>S: connected → S reads DB/logs directly (read-only)
    else Non-Direct-Connect
        S->>E: /info one precise read
        E-->>S: relays answer
    end
    S->>O: diagnosis + proposed fix (or reroute/escalate)
    O->>E: /validate conclusion?  then /approve recommended fix
    E-->>O: approved
    Note over E: HUMAN applies the fix in production
    E-->>O: applied & verified
    O->>J: write root cause + resolution (enriches history)
```

**Two human gates:** a light **validation** of the conclusion, and a hard **approval** of the recommended fix — with the human **always applying** it.
