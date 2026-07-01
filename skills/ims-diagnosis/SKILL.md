# Skill: ims-diagnosis

IMS (Inventory Management System) domain diagnosis guide.

## What IMS is

IMS is our inventory integrity gate. It enforces correct counts through **cycle counting** as orders are fulfilled. When a cycle count fails (actual vs. expected quantities don't reconcile), **IMS deliberately halts fulfillment at that point** until a correction is made.

**Key insight:** an IMS halt is not a bug. It is a correct, intentional hold. Never diagnose a software fault while an IMS hold is active.

## Why IMS cases get routed here

IMS incidents arrive in two ways:
1. **Direct route** — the Jira ticket explicitly mentions inventory discrepancy, cycle count, IMS hold, or count correction
2. **Reroute from another domain** — another subagent found `ims_hold_active = true` on the entity and sent it here

## Check for an active hold first (even here)

```
db_state_read(client_id="{client}", entity_type="order", entity_id="{id}")
→ look for: ims_hold_active, count_hold_reason, held_at_state
```

If `ims_hold_active = true`:
- `held_at_state` tells you where in the order lifecycle the hold was placed
- `count_hold_reason` identifies which item or bin triggered the discrepancy

## Failure modes

### count_discrepancy (most common)
Cycle count at pick completion found fewer items than expected. IMS halted the order.

Evidence:
- `ims_hold_active = true`, `count_hold_reason = "item {sku} expected {N} got {M}"`
- DB: IMS cycle count record shows the delta for a specific bin and item

Fix determination:
This is a **human investigation and correction** — the agent provides the specific bin ID, item SKU, expected quantity, actual quantity, and the IMS correction workflow. The engineer (or IMS admin) corrects the count in IMS to match reality. No SQL fix — the correction flows through IMS's own count-correction API/UI.

After the count is corrected, the IMS hold must be **explicitly cleared** before the order can advance. This is done via IMS (not by updating the order state directly).

### phantom_inventory
Items appear in IMS as available at a location, but physically they are absent (misplaced, consumed by a prior order, never put away).

Evidence:
- Operator reports "bin is empty" or "wrong item in bin" at pick station
- IMS shows the item as `available` at that location
- No recent decant record for a replacement quantity

Fix: Manual physical count by operator, IMS count correction to reflect actual quantity. If widespread → alert engineer to a systemic inaccuracy.

### double_pick
Same item recorded as picked twice — IMS count goes negative.

Evidence:
- IMS count for the item drops below zero after two picks
- Two separate pick completion events for the same bin+item combination in logs

Fix: IMS correction to restore the accurate count. Investigate whether the picking engine sent a duplicate completion event (if so, picking engine bug needs follow-up).

### ims_service_down
IMS service is not running. All cycle counts are failing, halting all orders at pick completion.

Evidence:
- Multiple orders stuck at `picking → picked` simultaneously with hold reason "IMS unavailable" or similar
- IMS health endpoint returning error or timeout

Fix: Restart IMS service. All held orders will resume cycle counting once IMS is back up. Do not advance order states manually — let IMS process them.

### count_hold_not_cleared
An engineer corrected the physical count but forgot to clear the IMS hold.

Evidence:
- Count in IMS now matches physical reality (engineer confirms)
- But `ims_hold_active` is still `true` — the hold was not released

Fix: Clear the IMS hold via the IMS hold-release interface. This is a workflow step the engineer may have missed.

## IMS hold and order states — the dependency

| Order lifecycle state | IMS halt possible | Check IMS if |
|---|---|---|
| validated → prioritized | Yes | Order won't advance despite WES running |
| prioritized → released | Yes (most common) | Order stuck in `prioritized` |
| released → picking (tote created→open) | Yes | Order stuck in `released`, no storage issue |
| picking → picked (tote picking→complete) | Yes (very common) | Order/tote stuck after operator picked |

## What the agent produces

For IMS incidents, the output is not an SQL UPDATE but a **count correction dossier**:
```
Entity:     order {id}, held at state '{held_at_state}'
Hold reason: item {sku} in bin {bin_id}
             Expected: {N}  Actual in IMS: {M}
             Last physical count: {timestamp or unknown}

Correction needed:
  Verify physical count of {sku} at bin {bin_id}
  Update IMS count to match physical reality: SET count = {actual}
  Then: release IMS hold on order {id} via IMS hold-management screen

After correction: order will resume at '{held_at_state}' → '{next_state}' automatically.
```

## Evidence gathering order
1. `history_search` — "IMS hold order {id}" or "count discrepancy {sku}" (recurring by SKU/bin)
2. `db_state_read` — order state, ims_hold_active, count_hold_reason, held_at_state
3. `db_state_read` — IMS count record for the specific item + bin
4. `log_read` — IMS cycle count logs around the time the hold was placed
5. `github_read` — IMS count-correction flow for this client (hold-release API/UI)
