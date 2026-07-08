# Glossary: GTP Decant (Good-to-Pick Station Decant)

| Term | Definition |
|---|---|
| `GTP Decant` | Good-to-Pick station decant; the inbound process where supplier deliveries are scanned into bins and placed into ASRS |
| `bin` | Physical container for items; stored in ASRS and retrieved to pick ports for operator picking |
| `decant station` | Physical station where operators scan inbound supplier items into bins |
| `supplier delivery manifest` | Document listing items expected in a supplier delivery; used to verify scan completeness |
| `placement request` | Request sent from decant software to ASRS to store a bin in the storage grid |
| `placement confirmed` | ASRS acknowledgement that a bin has been successfully stored and is available for retrieval |
| `bin_not_placed` | Most common decant failure: bin was scanned at decant station but ASRS did not confirm placement |
| `item_scan_failure` | Item barcode failed to scan at decant station; item was physically placed in bin but not recorded in the system |
| `wrong_bin_placement` | Item placed in the wrong bin, or bin placed in wrong ASRS storage location |
| `decant_service_down` | Decant station software not running; no new bins are being received; all inbound supply is blocked |
| `asrs_placement_rejected` | ASRS refused to accept a bin during placement (wrong bin type, storage full, grid cell occupied); reroute to ASRS subagent |
| `bin.state` | Lifecycle state of a bin: created → receiving → placed → available → at_port → returned → empty → damaged/missing |
| `receiving` | Bin state: supplier items are being scanned into the bin at the decant station |
| `placed` | Bin state: bin has been handed to ASRS and placement is confirmed |
| `available` | Bin state: bin is stored in ASRS and available for pick retrieval |
| `at_port` | Bin state: bin has been retrieved to a pick port; operator is picking from it |
| `damaged` / `missing` | Terminal bin error states requiring physical recovery |
| `bin_unavailable` | Downstream blocker on released→picking caused by a decant failure; the bin does not exist in storage |
