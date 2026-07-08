# Glossary: WES (Warehouse Execution System)

| Term | Definition |
|---|---|
| `WES` | Warehouse Execution System — the orchestration engine that prioritizes and releases orders to the picking engine |
| `prioritized → released` | WES-owned stuck transition; the order has been queued for picking but WES has not yet emitted the release signal |
| `release signal` | Message WES sends to the picking engine to begin picking an order |
| `ack` | Acknowledgement the picking engine sends back to WES confirming it received the release signal |
| `lost_ack` | WES emitted the release signal but never received acknowledgement; picking engine may already be working the order |
| `consumer_down` | WES consumer thread is not running; no messages of any kind are being processed |
| `WES consumer thread` | The thread that runs WES's message-consumption loop |
| `priority_queue_stall` | Order is in the WES priority queue but WES is not emitting release signals; often maintenance mode or queue processor paused |
| `priority_queue_position` | DB field: order's position in the WES priority queue; > 0 means it is queued |
| `ims_hold` | IMS has placed a count-hold; WES is waiting for IMS to clear it — this is not a WES bug, reroute to IMS |
| `ims_hold_active` | DB field: boolean flag indicating an active IMS count-hold on the order |
| `count_hold_reason` | DB field: reason code identifying which item or bin triggered the IMS hold |
| `picking_engine_busy` | Picking engine inbound channel is saturated; release was sent and is queued but not yet consumed |
| `picking engine` | Downstream system that receives released orders from WES and drives GTP Picking station work |
| `ack listener` | WES component that receives acknowledgements from the picking engine |
| `maintenance mode` | WES operational state where release signals are suppressed; check DB for maintenance-mode flag |
| `double-pick risk` | Risk of emitting a second release signal to the picking engine when one was already sent — never re-emit without confirming the order is not already in the picking engine's active queue |
