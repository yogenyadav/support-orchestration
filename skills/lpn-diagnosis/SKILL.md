# Skill: lpn-diagnosis

LPN (Label/Printer — License Plate Number printing) domain diagnosis guide.

## What LPN is

LPN covers the **print-and-apply labeling stage** at the end of the fulfillment flow. After packing, the WCS directs a print-and-apply unit to print a shipping label (with carrier barcode/LPN) and apply it to the package. The station then acks back to WCS.

"LPN" refers both to the printed label (License Plate Number — the carrier's package identifier) and the printers and apply mechanisms at the labeling station.

## Owned lifecycle positions

LPN owns:
- Order: `packed → labeled`
- Tote: `packed → labeled`

## Trigger: how labeling works

WCS sends a print command to the labeling station controller (transport varies by client — usually TCP or REST). The print-and-apply unit:
1. Receives print job with LPN data
2. Prints label on thermal printer
3. Applies label via mechanical arm
4. Acks completion to WCS

## Failure modes

### hardware_fault_printer (most common)
Label printer is offline, out of labels, out of thermal ribbon, or has a paper jam.

Evidence:
- Log: "print job sent to station {id}" but no ack / "printer offline" error
- Multiple packages stuck at `packed` simultaneously (affects all packages routed to this station)
- Printer status endpoint or station terminal shows error state

Fix: Physical printer intervention by engineer or operator:
- Replace label stock or thermal ribbon
- Clear paper jam
- Power-cycle the printer

No DB fix needed — once printer is back online, WCS will retry the print job.

### hardware_fault_arm
Print-and-apply arm mechanical failure — arm can't apply the label after printing.

Evidence:
- Log: "label printed" but no "label applied" event
- Station alerts: arm fault, E-stop, sensor error, or label mis-feed
- May be single-station or systemic depending on hardware

Fix: Physical field-engineer intervention for the mechanical arm. The agent escalates to the engineer with the station ID and the fault code from WCS logs.

### connectivity
WCS cannot reach the labeling station controller (network or socket issue).

Evidence:
- Log: "print command failed — connection refused" or timeout to station IP
- No other errors from the same station in recent history (sudden failure)
- Other WCS operations to different stations succeed

Fix:
- Check network path from WCS host to labeling station controller IP
- Reroute to infra if the network path is the issue
- May require restarting the station controller

### data_missing
Label data not available — order details needed for label generation (carrier barcode, ship-to address, order number) are absent or malformed.

Evidence:
- Log: "label generation failed — missing field {field_name}" or template error
- Specific to one order (not multiple) — systemic data issues would affect all
- Order record in DB is missing a required field for label generation

Fix: Identify the missing data field and its source. This is usually an upstream data issue (client WMS didn't include a field) rather than a hardware problem.

### label_template_mismatch
The label template configured for this client doesn't match what the carrier expects (wrong format, wrong barcode type, wrong dimensions).

Evidence:
- Labels are printing but being rejected at carrier scanning (reported by client)
- Recent client overlay deployment changed the label template config
- Specific carrier or service type affected, not all labels

Fix: Template config correction in the client overlay. Check `github_read` on the client org for the label template config file.

## Pattern: batch impact

A printer fault or connectivity issue affects **all packages routed to that station**, not just one. If multiple orders are stuck at `packed` simultaneously and they share a station:
- This is almost certainly a hardware or connectivity fault at the station, not an order-specific issue
- List the affected orders and confirm they all share the same station before diagnosing further

## Hardware vs. software discrimination

| Symptom | Responsibility |
|---|---|
| Printer out of labels/ribbon, paper jam | Operator at station (consumables) |
| Print arm mechanical failure, E-stop | Field engineer (hardware) |
| Station controller offline, network drop | infra subagent or LPN connectivity fix |
| Label template wrong, data missing | LPN software / WCS config fix |
| WCS not sending print commands | WCS subagent |

## Evidence gathering order
1. `history_search` — "printer stuck {client_id}" or "labeled transition failure"
2. `phoenix_resolve` — connectivity tier
3. `db_state_read` — order/tote state, which labeling station is assigned
4. `log_read` — WCS labeling station logs: print commands, acks, error codes
5. `github_read` — client label template config and station controller adapter
6. If multiple orders affected: pattern-match across orders to identify shared station
