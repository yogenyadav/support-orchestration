# Skill: gtp-picking-diagnosis

GTP Picking (Good-to-Pick station picking) domain diagnosis guide.

## Owned lifecycle positions

GTP Picking owns two transitions on the order lifecycle:
- **released → picking** (bin retrieval and station assignment)
- **picking → picked** (operator completion + IMS cycle count)

And three transitions on the tote lifecycle:
- **created → open_for_picking**
- **open_for_picking → picking_in_progress**
- **picking_in_progress → picking_complete**

## Check IMS hold FIRST (mandatory at picking→picked and tote picking_in_progress→picking_complete)

IMS cycle count runs at pick completion. A count discrepancy **deliberately halts** the tote and order here — this is correct behavior, not a software fault.

```
db_state_read(client_id="{client}", entity_type="order", entity_id="{id}")
→ look for: ims_hold_active, count_hold_reason
```

If an IMS hold is active → **reroute to IMS**. Do not diagnose picking engine further.

## Failure modes by transition

### released → picking

**storage_unavailable**
ASRS (Automated Storage and Retrieval System) not responding to bin retrieval request.

Evidence:
- Log: "bin retrieval request sent for order {id}" but no "bin at port" event follows
- DB: order state is `released` but no bin retrieval record exists in storage layer

Fix: Check ASRS health. If ASRS is down → reroute to ASRS subagent.

**bin_unavailable**
Required bin is not in storage — was never placed during decant, is damaged, or was misplaced.

Evidence:
- ASRS returns "bin not found" or "bin in error state"
- No active decant record for the bin in the decant layer DB

Fix: Escalate to engineer with specific bin ID. Physical recovery or re-decant needed.

**service_down**
Picking engine service not running or unresponsive.

Evidence:
- Log: no picking engine activity for any order in the last N minutes
- Multiple orders stuck in `released` simultaneously (not just one)
- Health check endpoint returning 5xx or timeout

Fix: Restart picking engine service. Verify: orders begin advancing within 2 minutes.

**capacity**
No available pick ports or stations — all are occupied.

Evidence:
- DB: all pick station slots show `occupied` status
- Orders queued in `released` but picking engine is processing other orders (not crashed)

Fix: Wait for stations to clear, or alert engineer to staffing/throughput bottleneck.

### picking → picked

**ims_hold** (check first — most common here)
IMS cycle count discrepancy. Reroute to IMS.

**human_delay**
Operator hasn't completed picking. Not a software fault.

Evidence:
- Tote is in `picking_in_progress` for longer than expected operator cycle time
- No error in picking engine logs
- Pick station terminal is responsive

Fix: Confirm with engineer whether operator is aware and working.

**inventory_mismatch**
Wrong items or empty bin at pick station — operator cannot complete the pick.

Evidence:
- DB: bin contents don't match pick instruction
- Operator may have raised a flag at station terminal
- Log: "item not found" or "quantity mismatch" at station

Fix: IMS review needed. The bin's contents must be reconciled before the pick can complete.

**lost_ack**
Operator completed picking but picking engine failed to record completion, or the ack to WES was lost.

Evidence:
- DB: tote shows `picking_complete` in picking engine DB but order still shows `picking` in WES
- Log: "pick complete recorded for tote {id}" but no "ack sent to WES" or "ack received by WES"

Fix: Determine if WES needs to be notified to re-poll or if the order state can be advanced directly. Confirm with engineer.

## ASRS client variants

The ASRS implementation varies by client (AutoStore vs. Knapp vs. others). Before diagnosing:
1. Read `github_read` for the client org to identify which ASRS adapter is in use
2. Each ASRS has a different API, health endpoint, and failure signature
3. If the ASRS is the root cause, reroute to the ASRS subagent — it owns bin storage/retrieval

## Evidence gathering order
1. `history_search` — "order {id} stuck released" or "order {id} stuck picking" (recurring pattern)
2. `phoenix_resolve` — connectivity tier
3. `db_state_read` — order + tote state, IMS hold flag
4. `db_state_read` — pick station status, queue depth
5. `log_read` — picking engine logs for the entity ID and any error strings
6. `github_read` — picking engine code + ASRS adapter for client
