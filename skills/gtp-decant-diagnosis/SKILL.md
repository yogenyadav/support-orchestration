# Skill: gtp-decant-diagnosis

GTP Decant (Good-to-Pick station decant) domain diagnosis guide.

## What decant is

Decant is the inbound process: supplier deliveries arrive and items are placed into bins, which are then put into the ASRS for storage. It owns the **earliest lifecycle stage** — before orders can be picked, the bins must exist and be correctly placed.

## Owned lifecycle position

GTP Decant owns the **inbound / bin-placement** phase. No order lifecycle transitions are numbered here, but a failed decant manifests as a **bin_unavailable** blocker on the order `released → picking` transition.

Key entity: **bin** (or container, depending on client naming)

Bin lifecycle (logical):
1. `created` — bin record allocated, physical bin labelled
2. `receiving` — supplier items being scanned into the bin at decant station
3. `placed` — bin handed to ASRS for storage; ASRS confirms placement
4. `available` — bin is stored and available for retrieval in a pick
5. `at_port` — bin retrieved to a pick port; operator picking from it
6. `returned` — bin returned to ASRS after picking; contents updated
7. `empty` — bin has no remaining items; scheduled for replenishment
8. `damaged` / `missing` — terminal error states

## Failure modes

### bin_not_placed (most common decant failure)
Bin was scanned at decant station but ASRS did not confirm placement.

Evidence:
- DB: bin record shows `receiving` or `created` but no `placed` or `available` state
- Log: "placement request sent" but no "placement confirmed" from ASRS
- ASRS may have rejected the bin (wrong barcode, wrong bin type, ASRS full)

Fix: Determine whether the bin was physically placed (ASRS issue) or not yet placed (operator process issue). Route to ASRS if storage system rejected it.

### item_scan_failure
Item barcode failed to scan at decant station; item was placed in bin but not recorded.

Evidence:
- DB: bin contents record doesn't match expected supplier delivery
- Log: "scan error" or "unrecognised barcode" at decant station terminal
- Supplier delivery manifest shows items that aren't in any bin record

Fix: Manual re-scan or manual item entry at decant station by operator. IMS count correction may follow.

### wrong_bin_placement
Item placed in the wrong bin, or bin placed in wrong storage location by ASRS.

Evidence:
- IMS discrepancy on a specific item in a specific bin
- Pick instruction directs operator to bin that doesn't contain the expected item

Fix: IMS reconciliation. May require physical bin inspection. Alert engineer with specific bin IDs.

### decant_service_down
Decant station software not running — no new bins are being received.

Evidence:
- Log: no decant activity across any bins in the recent window
- Multiple bins show `receiving` or `created` but none are advancing to `placed`
- Decant station terminal shows error state

Fix: Restart decant station software or alert engineer. This blocks all inbound supply.

### asrs_placement_rejected
ASRS refused to accept a bin during placement (wrong type, wrong location, ASRS full).

Evidence:
- ASRS API returned an error for the placement request
- Bin is at the placement point but not confirmed stored

Fix: Reroute to ASRS subagent — this is an ASRS-layer issue, not a decant-layer issue.

## IMS relationship

Decant errors almost always lead to IMS discrepancies downstream. A bin placed with wrong contents, or not placed at all, creates inventory divergence that IMS will catch at pick time. If IMS diagnosed a hold and the hold is traced back to a specific bin's contents — look at the decant history for that bin.

## Evidence gathering order
1. `history_search` — "bin {id} placement failed" or "decant station stuck"
2. `phoenix_resolve` — connectivity tier
3. `db_state_read` — bin state, bin contents record vs. expected supplier delivery
4. `log_read` — decant station logs for the bin ID, placement events, scan errors
5. `github_read` — decant service code + ASRS placement adapter for this client
6. If ASRS rejected: reroute to ASRS subagent
