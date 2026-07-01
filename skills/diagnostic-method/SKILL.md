# Skill: diagnostic-method

Universal stuck-entity diagnostic method used by every domain subagent.

## The method (always in this order)

### 1. Search history first
Call `mcp__support__history_search` with a description of the symptom.
Most incidents recur. A prior resolved ticket is the fastest path to root cause.

### 2. Resolve connectivity
Call `mcp__support__phoenix_resolve` to know what tools are available.
- `direct_connect` → DB and logs readable after human opens session
- `human_relay` → no prod access; ask the engineer via /ask for each data point
- `s3_logs` → logs in AWS S3; DB via human relay

### 3. Read the entity's current state
Call `mcp__support__db_state_read`. This is cheap and often decisive.
The entity's actual DB state tells you exactly where on the lifecycle it is stuck.

### 4. Locate the stuck transition on the lifecycle map
Match `entity_current_state` to the transition that should fire next.
The lifecycle map gives you: owning_domain, expected trigger, candidate blockers.

### 5. Work the blocker checklist
For the stuck transition, check each candidate blocker in priority order.
Read evidence from DB, logs, or code. Eliminate blockers you can rule out.

### 6. Determine root cause
One specific, falsifiable root cause. Not "something in WES" but "WES consumer
thread dead since 14:32" or "IMS count-hold on order 12345 placed at 09:15".

### 7. Propose a fix
Only when you have direct evidence. The fix must be:
- Specific (table + row + target state, or specific service to restart)
- Reversible (or clearly stated if not)
- Verifiable (what the engineer should observe to confirm success)

## Termination criteria
- `propose_to_human` — root cause confirmed + fix supported by direct evidence
- `reroute:DOMAIN` — evidence clearly points to a different domain; name it exactly
- `escalate` — insufficient evidence after exhausting all available tools
- `need_info` — blocked on one specific data point; state the exact question
