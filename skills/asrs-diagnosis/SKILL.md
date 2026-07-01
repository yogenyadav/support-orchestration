# Skill: asrs-diagnosis

ASRS (Automated Storage and Retrieval System) domain diagnosis guide.

## What ASRS is

ASRS is the automated storage layer — it stores bins and retrieves them to pick ports on demand. It is a physical/software hybrid: the hardware is managed by the ASRS vendor (AutoStore, Knapp, or other), and our software integrates via a vendor API or adapter.

**Critical:** ASRS implementations vary by client. The client's GitHub overlay determines which ASRS vendor, which adapter version, and which API is in use. Always read the client's ASRS adapter before diagnosing.

## Owned lifecycle position

ASRS owns bin storage and retrieval. It does not own order or tote states directly, but it is a **critical dependency**:
- `released → picking` depends on `bin.state = at_port` (ASRS must deliver the bin)
- `picking_complete → picking_complete` (bin returned to ASRS after pick)
- All decant placements depend on ASRS accepting the bin

## Failure modes

### storage_unavailable (most common)
ASRS system is not responding to bin retrieval or placement requests.

Evidence:
- Log: "retrieval request sent to ASRS for bin {id}" but no confirmation follows
- ASRS adapter log: connection timeout, API error, or no response
- Multiple orders simultaneously stuck in `released` waiting for bins

Fix determination:
- If ASRS API is unreachable: connectivity issue between our software and ASRS vendor system
  → Check network/firewall between our picking engine and ASRS controller
  → Reroute to infra if network is the issue
- If ASRS controller is running but returning errors: ASRS vendor incident
  → Escalate to engineer to contact ASRS vendor support

### bin_retrieval_timeout
Retrieval request was accepted by ASRS but the bin never arrived at the pick port within the expected time window.

Evidence:
- ASRS confirmed the retrieval request ("request accepted")
- No "bin at port" event after expected retrieval duration (check client config for timeout value)
- ASRS status for the bin shows it as "in transit" or stuck in a grid cell

Fix: ASRS internal jam or grid blockage — vendor-level issue. Escalate to engineer + ASRS vendor if the bin is stuck in-grid.

### bin_placement_failed
During decant, ASRS rejected a bin placement (wrong bin type, storage full, or grid cell occupied).

Evidence:
- Decant log: "placement request sent" → ASRS returned error
- ASRS error code (varies by vendor — read client's ASRS adapter for error mapping)
- Specific grid cell or port status

Fix: Depends on error:
- Storage full: engineer must clear space or remap bins
- Wrong bin type: operator placed wrong physical bin → manual correction at decant station
- Grid cell occupied: ASRS vendor issue

### asrs_port_unavailable
All pick ports are occupied; ASRS cannot deliver any more bins.

Evidence:
- DB: all port slot records show `occupied`
- ASRS accepting retrieval requests but queuing them (delivery delayed, not failed)
- Orders stuck in `released` but picking engine is otherwise healthy

Fix: Throughput bottleneck — not a failure. Alert engineer to port utilization. If a port is stuck in `occupied` because a prior pick wasn't closed, that's a GTP Picking issue.

### adapter_misconfiguration
The ASRS adapter in the client overlay is misconfigured — wrong API endpoint, wrong auth credentials, wrong bin type mapping.

Evidence:
- ASRS API returns 401/403 or "unknown bin type" errors
- Recent deployment to the client overlay (check `git log` on the client GitHub org)
- Works for one ASRS but not another in the same warehouse (multi-zone clients)

Fix: Correct the adapter config in the client overlay. Human applies after review.

## Client ASRS variants

| ASRS vendor | Common identifiers in code | Known failure signatures |
|---|---|---|
| AutoStore | `autostore`, `AutoStoreAdapter`, `autostore-api` | Grid cell jams, robot failures, port busy |
| Knapp | `knapp`, `KnappAdapter`, `knapp-api` | Conveyor faults, port allocation errors |
| Other | Read client overlay to identify | Vendor-specific |

Always read `github_read` on the client org for the ASRS adapter before assuming failure mode.

## Evidence gathering order
1. `history_search` — "ASRS {client_id} bin retrieval" or "AutoStore/Knapp down"
2. `phoenix_resolve` — connectivity tier
3. `log_read` — ASRS adapter logs: retrieval/placement requests, API responses, error codes
4. `db_state_read` — bin state, port occupancy, retrieval queue depth
5. `github_read` — client org ASRS adapter code (identify vendor, API endpoint, error mapping)
6. If network is implicated: reroute to infra subagent
