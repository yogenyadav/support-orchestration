# Glossary: LPN (Label/Printer — License Plate Number)

| Term | Definition |
|---|---|
| `LPN` | License Plate Number — the carrier's package identifier printed on the shipping label; also refers to the print-and-apply labeling system and station |
| `packed → labeled` | LPN-owned order and tote lifecycle transition; WCS directs the print-and-apply unit to label the package |
| `print-and-apply unit` | Physical device that prints a shipping label on a thermal printer and applies it to the package via a mechanical arm |
| `labeling station controller` | Software/hardware controller at the labeling station; receives print commands from WCS via TCP or REST |
| `print job` | WCS command sent to the labeling station controller containing LPN data for label generation |
| `thermal printer` | Printer used to produce shipping labels; can be out of labels, out of ribbon, or have a paper jam |
| `station ID` | Identifier for a specific physical labeling station in the warehouse |
| `hardware_fault_printer` | Most common LPN failure: label printer is offline, out of labels, out of thermal ribbon, or has a paper jam |
| `hardware_fault_arm` | Print-and-apply arm mechanical failure; arm cannot apply the label after printing; requires field engineer |
| `connectivity` | WCS cannot reach the labeling station controller (network or socket issue) |
| `data_missing` | Label data absent or malformed — carrier barcode, ship-to address, or order number missing from the order record |
| `label_template_mismatch` | Label template configured for this client doesn't match carrier expectations (wrong format, barcode type, or dimensions) |
| `label stock` | Physical label roll loaded into the thermal printer |
| `thermal ribbon` | Ink ribbon used by thermal-transfer printers; when exhausted, labels print blank |
| `E-stop` | Emergency stop signal from a mechanical arm or station hardware |
| `batch impact` | Pattern: a printer fault or connectivity failure affects all packages routed to that station simultaneously, not just one order |
| `station controller` | The per-station software process that manages communication between WCS and the physical print-and-apply hardware |
