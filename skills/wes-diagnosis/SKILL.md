# Skill: wes-diagnosis

WES (Warehouse Execution System / Orchestration Engine) domain diagnosis guide.

## Owned lifecycle position
WES owns the stuck transition: **prioritized → released**

## Check IMS hold FIRST
Before any WES diagnosis: read the order's DB state and check for an IMS count-hold.
If a hold exists → reroute to IMS. This is the most common false-WES incident.

```
db_state_read(client_id="{client}", entity_type="order", entity_id="{id}")
→ look for: ims_hold_active, count_hold_reason
```

## Failure modes (blocker_class)

### lost_ack
WES emitted the release signal to the picking engine, but never received the acknowledgement.
The picking engine may already be working the order.

**Evidence:**
- Log: "release emitted for order {id}" but no "ack received for order {id}"
- DB: picking engine's active queue contains order {id}

**Fix:** Confirm picking engine has order in active state → restart WES ack listener →
WES re-polls picking engine → order advances. No DB UPDATE needed.

### consumer_down
The WES consumer thread is not running. No messages of any kind are processed.

**Evidence:**
- Log: consumer.stop() or "Consumer terminated" without corresponding restart
- Multiple orders may be stuck, not just the reported one

**Fix:** Restart WES consumer service. Human applies. Verify: orders begin moving in < 2 min.

### priority_queue_stall
The order is in the WES priority queue but WES is not emitting release signals.
Could be WES in maintenance mode or queue processor paused.

**Evidence:**
- DB: order has priority_queue_position > 0
- Log: no "release emitted" messages for any order in the past N minutes

**Fix:** Check WES maintenance-mode flag in DB. Operator unpauses WES or restarts consumer.

### ims_hold
IMS has placed a deliberate count-hold. WES is waiting for IMS to clear it.
**This is not a WES bug — reroute to IMS.**

### picking_engine_busy
Picking engine inbound channel saturated. Release was sent and is queued but not yet consumed.

**Evidence:**
- Log: release emitted for order {id}; picking engine queue depth > threshold
- DB: picking engine queue shows backlog

**Fix:** Wait for picking engine to drain OR escalate to engineer. Do NOT re-emit the release
(double-pick risk). If backlog doesn't clear, picking engine may need investigation.

## Evidence gathering order
1. `history_search` — "order {id} stuck prioritized" (prior lost_ack or consumer_down)
2. `phoenix_resolve` — connectivity tier and log posture
3. `db_state_read` — order state, ims_hold flag, priority_queue_position
4. `log_read` — search for "release emitted", "ack received", consumer thread status
5. If picking_engine_busy suspected: `db_state_read` picking engine queue depth
