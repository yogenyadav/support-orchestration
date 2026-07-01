# Skill: esb-diagnosis

ESB (Enterprise Service Bus — ActiveMQ + Apache Camel) domain diagnosis guide.

## What ESB is

ESB is the messaging backbone built on **ActiveMQ** (transport) with **Apache Camel** layered on top for routing. Messages travel on **channels** (ActiveMQ queues/topics); Camel routes define which consumer picks up each message type and where it goes.

ESB is the first thing a client order touches when it enters our stack. It receives orders from the client's WMS and routes them to WES/OE for prioritization.

## Owned lifecycle position

ESB owns the **received → validated** transition on the order lifecycle.

Trigger: Client WMS pushes an order onto the ESB inbound channel → Camel route validates the order structure → routes it to the WES intake queue.

## Check dead-letter queue FIRST

Before any other ESB diagnosis: check the **dead-letter queue (DLQ)**. A malformed order from the client WMS is silently moved to the DLQ without surfacing an error to any human. This is the most commonly missed ESB failure.

```
log_read(client_id="{client}", service="activemq", 
         keywords=["dead-letter", "DLQ", "order {id}"])
db_state_read(client_id="{client}", entity_type="order", entity_id="{id}")
→ check: dlq_reason, validation_error
```

If the order is in the DLQ → this is a **bad_input** failure — the WMS sent malformed data. Not an ESB bug; route findings back to the engineer for client communication.

## Failure modes

### stuck_queue (most common)
ActiveMQ queue has messages but no consumer is draining it. The order is sitting in the queue unprocessed.

Evidence:
- ActiveMQ management/log: queue depth > 0, consumer count = 0 on the WES-intake channel
- Log: no Camel route processing events for orders in the recent window
- Order record in DB shows `received` but no validation attempt

Fix: Restart the Camel consumer / ESB service. Once restarted, the queue drains and orders advance. Verify: queue depth drops to 0 within 2 minutes.

### consumer_down
The Camel route consumer for the WES-intake channel is not running.

Evidence:
- Camel route log: "route stopped" or "consumer terminated" without a restart
- ActiveMQ: messages are arriving on the channel but no consumer is registered
- May affect all orders from this client simultaneously

Fix: Restart the Camel route or ESB application. If the route stops repeatedly, there may be a misconfiguration causing it to crash on startup.

### bad_input / malformed_order
Client WMS sent an order payload that failed ESB validation.

Evidence:
- DLQ contains the order
- Camel log: "validation failed — missing field {field}" or "schema mismatch"
- Specific to one order (not systemic — other orders are flowing)

Fix: This is a **client-side issue**. The WMS needs to correct and re-send the order. Provide the engineer with the specific validation error so they can communicate it to the client.

### misconfiguration
Camel route config mismatch between base and client overlay — the route is configured incorrectly for this client's channel names, broker URL, or consumer group.

Evidence:
- ESB worked previously and a recent deployment changed the client overlay
- Log: "no route found for message type {type}" or connection to wrong broker URL
- Other clients' ESB instances are healthy — this is client-specific

Fix: Config correction in the client overlay. Read `github_read` on the client org for the Camel route config file. Human applies the corrected config and restarts.

### activemq_broker_down
The ActiveMQ broker itself is not running — no messages can be sent or received by any service.

Evidence:
- All ESB-dependent services reporting connection failure to ActiveMQ
- ActiveMQ admin console unreachable or health endpoint returning error
- All orders across all clients (or this client's entire site) are stuck

Fix: Restart ActiveMQ broker service. This is high-impact — all queued messages should be durable (persistent) and will resume processing when the broker restarts.

### channel_misconfiguration
Orders are arriving but going to the wrong channel (the Camel route isn't picking them up because they're on a different queue name than expected).

Evidence:
- ActiveMQ: messages visible on one queue but the consumer is listening on a differently named queue
- Orders arriving (WMS side confirms send) but no DB record of them in the ESB layer

Fix: Channel name alignment between WMS producer config and ESB consumer config. Check client overlay config and WMS integration spec.

## ActiveMQ channel naming

Channel names vary per client (part of the client overlay config). Before diagnosing:
- Read `github_read` on the client org for ActiveMQ/Camel configuration
- Identify: inbound order queue name, DLQ name, WES-intake queue name

## Evidence gathering order
1. `history_search` — "ESB stuck {client_id}" or "ActiveMQ consumer down" (recurring)
2. **Check dead-letter queue** — `log_read` for DLQ entries for this order
3. `phoenix_resolve` — connectivity tier
4. `log_read` — Camel route logs: consumer state, route processing events, validation errors
5. `log_read` — ActiveMQ broker logs: queue depth, consumer count, broker health
6. `db_state_read` — order record: is it in `received` state at all, or was it never ingested?
7. `github_read` — client overlay Camel route config + channel names

## ESB vs. WES distinction

A common triage mistake: the order is stuck at `validated` (not `received`). This means ESB already delivered it to WES — the issue is in WES, not ESB. Confirm the order's state before routing to ESB.

| Order state | Owning subagent |
|---|---|
| `received` (not advancing) | ESB — check queue and Camel route |
| `validated` (not advancing) | WES — ESB already did its job |
