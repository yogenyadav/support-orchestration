# Skill: wcs-diagnosis

WCS (Warehouse Control System) domain diagnosis guide.

## What WCS is

WCS routes totes physically through the warehouse from pick station through sortation, packing, labeling, and outbound. It communicates with WES over **TCP/IP sockets** (always — never ActiveMQ or REST for this channel). WCS runs as **C and C# Windows services** on a **Windows VM** managed by VMware vSphere, with **MS SQL Server** typically co-located on the same VM.

## Owned lifecycle positions

WCS owns the entire post-pick physical routing chain:
- Order: `picked → on_takeaway`, `on_takeaway → sorted`, `sorted → packed`, `packed → labeled`, `labeled → loaded`, `loaded → shipped`
- Tote: `picking_complete → on_conveyor`, `on_conveyor → at_sorter`, `at_sorter → at_packing`, `at_packing → packed`, `packed → labeled`, `labeled → dispatched`

## Hardware / software / infra discrimination (enforce this first)

The **hypervisor is the boundary**:

| Symptom | Owner |
|---|---|
| Sorter jam, E-stop, conveyor mechanics, diverter actuator | Field engineer (physical hardware) |
| VM down, vSphere host fault, disk/memory exhaustion, network at hypervisor level | **infra subagent** |
| Windows service crashed or hung, MS SQL issue, socket code bug, routing logic error | **WCS subagent (this skill)** |

Check whether the Windows service is alive and MS SQL is reachable before assuming hardware. Physical faults are rarer; they have distinct signatures (E-stop events, sensor fault codes in WCS logs, mechanical alert strings).

## WES ↔ WCS socket (always TCP)

All WES instructions arrive via TCP socket. Socket failures cause the most common WCS-layer stuck transitions.

**Socket health checklist:**
1. Is the socket connection established? (WCS log: "connection established" vs. "connection refused" / "connection reset")
2. Is WCS receiving WES messages? (WCS log: look for incoming instruction messages for the entity)
3. Did WCS send the ack back? (WCS log: "ack sent for order {id}" vs. no ack record)

If ack was sent but WES still thinks the entity is stuck → `lost_ack` on the WES side, not a WCS fault.

## Failure modes

### socket_failure
WES→WCS TCP socket dropped, half-open, or connection refused.

Evidence:
- WCS log: "connection reset by peer", "connection timed out", or "socket closed"
- Multiple totes/orders stuck at the same state simultaneously (single socket drop affects all)
- WES log: no ack received, timeout on send

Fix: Re-establish the TCP connection. On the WCS side: restart the socket listener service component. On WES: WES will retry when the socket is restored.

### wcs_service_down
The WCS Windows service is not running.

Evidence:
- Log: no WCS activity for any entity in recent window
- Multiple orders stuck across multiple transitions simultaneously (not just one)
- Windows Event Log (if accessible): service stopped, access violation, or crash dump

Fix: Restart the WCS Windows service. Human applies via RDP to the Windows VM or PowerShell remote. Command: `Restart-Service {WCSServiceName}` — confirm service name from GitHub code.

After restart: verify MS SQL connectivity before orders can advance.

### ms_sql_unreachable
WCS service is running but cannot connect to MS SQL Server.

Evidence:
- WCS log: "SQL connection failed", "cannot open database", or "login timeout"
- MS SQL service may be down on the same VM, or disk is full preventing SQL from writing

Fix: 
- If MS SQL service is down: restart it on the Windows VM
- If disk full: clear space and restart SQL (alert infra to disk monitoring)
- Route to infra subagent if the underlying cause is VM-level

### lost_ack (WCS side)
WCS completed its task (placed tote on conveyor, fired diverter) but the ack back to WES was dropped.

Evidence:
- WCS log: "task complete for tote {id}" and "ack sent"
- WES log: no ack received for that entity
- Entity is physically in the correct state but WES shows the prior state

Fix: The entity is already in the correct physical state. The orchestrator (or WES) needs to re-sync state — typically by WES re-polling WCS for the entity's current position, or by a direct order state update. Confirm with engineer.

### routing_error
WCS sent the tote to the wrong chute, packing station, or outbound lane.

Evidence:
- WCS log: chute assignment for entity {id} = station X, but station X was wrong (order goes to client Y)
- Client complains of mixed orders at a packing station
- Order ended up at wrong station and wasn't packed correctly

Fix: Determine the correct routing from the order record. WCS routing config may be misconfigured in the client overlay. Check `github_read` for routing table config.

### hardware_fault (field engineer, not WCS software)
Physical hardware failure — sorter jam, E-stop, diverter actuator fault, conveyor stop.

Evidence:
- WCS log: E-stop event, sensor fault code, "mechanical error at station {id}"
- Single physical point of failure — only totes near that point are stuck
- WCS service itself is running normally

Fix: Escalate to field engineer with the specific station, error code, and time of fault. This is outside software scope.

## MS SQL note

WCS is the **only domain that uses MS SQL** (all other domains use Oracle or Postgres depending on client). The MS SQL instance is typically on the same Windows VM as the WCS service. If disk is full or SQL is down, WCS fails silently — no socket errors, just no order state writes.

## Evidence gathering order
1. `history_search` — "WCS stuck {client_id}" or "socket failure WCS"
2. `phoenix_resolve` — connectivity tier
3. `log_read` — WCS logs for the entity ID, socket events, ack records, E-stop codes
4. `db_state_read` — order/tote state in MS SQL; check for WCS error or flag columns
5. `github_read` — WCS service name, restart procedure, routing config for client overlay
6. If VM/hypervisor suspected: reroute to infra subagent
7. If physical E-stop/mechanical fault: escalate to field engineer via /validate with fault code
