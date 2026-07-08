# Glossary: ESB (Enterprise Service Bus)

| Term | Definition |
|---|---|
| `ESB` | Enterprise Service Bus; the messaging backbone built on ActiveMQ + Apache Camel; first system a client order touches when it enters our stack |
| `ActiveMQ` | Message broker providing durable queues and topics for inter-service messaging |
| `Apache Camel` | Routing framework layered on ActiveMQ; Camel routes define which consumer picks up each message type and where it goes |
| `Camel route` | Apache Camel consumer definition specifying which queue to consume and where to route messages |
| `channel` | ActiveMQ queue or topic; named per-client in the overlay config |
| `inbound channel` | ActiveMQ queue on which the client WMS publishes incoming orders |
| `WES-intake queue` | ActiveMQ queue name for orders being handed from ESB to WES after validation |
| `dead-letter queue (DLQ)` | ActiveMQ queue where malformed or unprocessable orders are silently deposited; check this first before any ESB diagnosis |
| `dlq_reason` | DB field: reason code for why an order was moved to the DLQ |
| `validation_error` | DB field: specific validation failure that caused order rejection |
| `received → validated` | ESB-owned order lifecycle transition; ESB validates the order structure and routes it to WES |
| `stuck_queue` | Most common ESB failure: ActiveMQ queue has messages but consumer count = 0; no Camel route is draining it |
| `consumer_down` | Camel route consumer for the WES-intake channel is not running; orders arrive but are not processed |
| `bad_input / malformed_order` | Client WMS sent an order payload that failed ESB schema validation; order is in the DLQ; client must correct and re-send |
| `misconfiguration` | Camel route config mismatch between base and client overlay (wrong channel name, broker URL, or consumer group) |
| `activemq_broker_down` | ActiveMQ broker itself is not running; all queued messages across all clients are blocked; restart restores durable messages |
| `channel_misconfiguration` | Orders arriving on one queue name while the consumer listens on a differently named queue; orders arrive but are never ingested |
| `consumer group` | Group of Camel consumers sharing a subscription to a channel |
| `durable messages` | ActiveMQ persistence guarantee: messages queued before a broker restart are not lost and resume processing after restart |
| `WMS` | Client's Warehouse Management System; the upstream system that publishes orders onto the ESB inbound channel |
