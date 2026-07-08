# Glossary: ASRS (Automated Storage and Retrieval System)

| Term | Definition |
|---|---|
| `ASRS` | Automated Storage and Retrieval System; the physical/software hybrid that stores bins in a grid and retrieves them to pick ports on demand |
| `ASRS controller` | Vendor-managed control system for ASRS hardware; receives retrieval and placement requests from our software via API |
| `ASRS adapter` | Client-specific software adapter connecting our picking engine to the ASRS vendor's API; varies by client |
| `AutoStore` | ASRS vendor; code identifiers: `autostore`, `AutoStoreAdapter`, `autostore-api`; known failures: grid cell jams, robot failures, port busy |
| `Knapp` | ASRS vendor; code identifiers: `knapp`, `KnappAdapter`, `knapp-api`; known failures: conveyor faults, port allocation errors |
| `retrieval request` | Request sent to ASRS to deliver a specific bin from storage to a pick port |
| `placement request` | Request sent to ASRS to store a bin in its storage grid (sent during decant) |
| `bin.state = at_port` | Bin state indicating ASRS has delivered the bin to a pick port; required precondition for the released→picking transition |
| `in transit` | ASRS bin state indicating the bin is being moved within the grid; an extended in-transit state signals a jam |
| `pick port` | Physical port on the ASRS grid perimeter where bins are delivered for operator access |
| `storage_unavailable` | Most common ASRS failure: system not responding to retrieval or placement requests |
| `bin_retrieval_timeout` | Retrieval request was accepted by ASRS but the bin never arrived at the pick port within the expected time window; typically a grid jam |
| `bin_placement_failed` | ASRS rejected a bin during decant placement (wrong bin type, storage full, grid cell occupied) |
| `asrs_port_unavailable` | All pick ports are occupied; ASRS cannot deliver more bins; throughput bottleneck, not a failure |
| `adapter_misconfiguration` | ASRS adapter configured with wrong API endpoint, wrong auth credentials, or wrong bin type mapping |
| `grid cell` | Individual storage location within the ASRS grid where a bin can be stored |
| `storage full` | ASRS grid has no available cells for new bin placements |
