# Skill: evidence-gathering

How to gather evidence given the connectivity tier and tool layer available.

## Step 1 — Resolve the connectivity tier

Call `phoenix_resolve` first. The tier governs what tools are available:

| Tier | DB access | Log access | How to gather |
|---|---|---|---|
| `direct_connect` | Yes, after human opens session | Yes | Call `db_state_read` + `log_read` directly |
| `s3_logs` | Via human relay | Logs in AWS S3 | `log_read` with S3 posture; DB via `/ask` |
| `human_relay` | No prod access | No prod access | Every data point via `/ask` |

**Always resolve tier before calling any read tool.** Calling `db_state_read` on a `human_relay` client returns nothing — you must `/ask` the engineer instead.

## Step 2 — History first (always)

Before reading live data, call `history_search` with a symptom description:
```
history_search(query="order stuck {current_state} client {client_id}", top_k=5)
```
A prior resolved ticket with the same pattern is the fastest path to root cause and an already-validated fix. If history matches clearly → present to engineer as a candidate, still verify against live state.

## Evidence source priority

Work in this order per blocker hypothesis:

### Direct Connect clients
1. `db_state_read` — cheapest; often decisive. Read the stuck entity and any dependency entities.
2. `log_read` — search for the transition trigger event and any error around it.
3. `github_read` — understand what the code should do for this transition. Read base + client overlay.
4. `/validate` the conclusion with the engineer before `/approve`.

### S3-log clients
1. `db_state_read` — not available; ask via `/ask` for entity state fields one at a time.
2. `log_read` — call with S3 posture (logs in `{client_id}` S3 bucket).
3. `github_read` — available universally.

### Human-relay clients
No live tool reads. Build the evidence picture via `/ask`:
- Ask for one specific data point per message
- Ask for exact field values, not descriptions ("what is the value of the `status` column for order 12345?")
- Accumulate answers in the Case object before concluding

## Reading DB state

```
db_state_read(
    client_id="{client}",
    entity_type="order",     # or "tote", "bin", "inventory"
    entity_id="{id}"
)
```
Key fields to look for (exact names vary per client — read from GitHub first):
- Current state column
- IMS hold flag / count-hold reason
- Timestamps of last state transition
- Any error or exception column

## Reading logs

```
log_read(
    client_id="{client}",
    service="{service_name}",
    time_range="{start}/{end}",     # narrow to 15 min around the stall
    keywords=["order {id}", "ack", "error", "exception"]
)
```
- Narrow the time range to the window when the entity stopped advancing (last state change timestamp ± 15 min)
- Search for the entity ID, the expected trigger string, and error keywords together
- Look for the trigger event (e.g., "release emitted") and whether its ack was logged

## Reading code

```
github_read(
    client_id="{client}",
    repo="base",             # or the client org
    path="services/{domain}/",
    query="state transition {from_state} to {to_state}"
)
```
- Read base first, then the client overlay for the relevant service
- Look for the state transition handler and what it expects upstream
- Identify queue names, ack endpoints, DB table/column names for live data reads

## Per-tool one-open-request rule

In `human_relay` mode, send exactly one `/ask` at a time and wait for the response before the next. Multiple simultaneous asks are ignored or confuse the engineer. Queue them internally and issue one at a time.

## Evidence sufficiency for fix determination

Evidence is sufficient when:
- Root cause is identified with direct evidence (not inference alone)
- The specific entity, table, row, and target state are known
- The fix is reversible or the engineer is informed it is not
- A verification step exists (what the engineer observes to confirm success)

If evidence points to a different domain, issue `reroute:{DOMAIN}` with the specific evidence.
