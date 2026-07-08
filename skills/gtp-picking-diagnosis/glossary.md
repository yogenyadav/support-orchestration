# Glossary: GTP Picking (Good-to-Pick Station Picking)

| Term | Definition |
|---|---|
| `GTP Picking` | Good-to-Pick station picking; owns the order transitions released→picking and picking→picked, and the tote picking lifecycle |
| `released → picking` | Transition where the picking engine retrieves a bin from ASRS and assigns a pick station |
| `picking → picked` | Transition where the operator completes picking and IMS cycle count runs |
| `open_for_picking` | Tote state: tote record created and ready to accept picks |
| `picking_in_progress` | Tote state: operator is actively picking items into the tote |
| `picking_complete` | Tote state: all picks done; IMS cycle count has cleared |
| `pick port` | Physical port on the ASRS where bins are delivered for operator access |
| `pick station` | Physical station where an operator picks items from retrieved bins |
| `bin retrieval` | ASRS operation that moves a stored bin from the grid to a pick port |
| `storage_unavailable` | ASRS not responding to a bin retrieval request; reroute to ASRS subagent |
| `bin_unavailable` | Required bin is not in storage — never placed during decant, damaged, or misplaced; physical recovery or re-decant needed |
| `service_down` | Picking engine service not running or unresponsive; multiple orders stuck simultaneously in `released` |
| `capacity` | All pick station slots are occupied; picking engine is healthy but throughput is bottlenecked |
| `human_delay` | Operator has not completed picking within expected cycle time; not a software fault |
| `inventory_mismatch` | Wrong items or empty bin at pick station; operator cannot complete the pick; IMS review needed |
| `lost_ack` | Operator completed picking but picking engine failed to record completion, or the ack to WES was lost |
| `IMS cycle count` | IMS inventory verification that runs automatically at pick completion; a count discrepancy deliberately halts the order |
| `ASRS adapter` | Client-specific software adapter for the ASRS vendor's API; varies by client (AutoStore, Knapp, others) |
| `ims_hold_active` | DB field: boolean indicating an active IMS hold; check this first at picking→picked |
