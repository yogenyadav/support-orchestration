# Glossary: IMS (Inventory Management System)

| Term | Definition |
|---|---|
| `IMS` | Inventory Management System; the inventory integrity gate that enforces correct item counts through cycle counting |
| `cycle counting` | IMS process of verifying actual vs. expected item quantities at specific points in fulfillment; a discrepancy triggers a deliberate hold |
| `IMS hold` | Deliberate fulfillment halt placed by IMS when a cycle count discrepancy is detected; it is intentional correct behavior, not a bug |
| `ims_hold_active` | DB field: boolean indicating an active IMS count-hold on the entity |
| `count_hold_reason` | DB field: identifies the specific item SKU and bin that triggered the discrepancy (e.g., "item {sku} expected {N} got {M}") |
| `held_at_state` | DB field: order lifecycle state at which the IMS hold was placed |
| `hold-release` | IMS operation to explicitly clear an active hold after count correction; must be done via IMS, not by updating the order state directly |
| `count_discrepancy` | Most common IMS failure: cycle count found fewer items than expected; IMS halted the order |
| `phantom_inventory` | Items appear in IMS as available at a location but are physically absent (misplaced, consumed, never put away) |
| `double_pick` | Same item recorded as picked twice; IMS count drops below zero |
| `ims_service_down` | IMS service not running; all cycle counts failing; all orders stuck at picking→picked simultaneously |
| `count_hold_not_cleared` | Engineer corrected the physical count but did not clear the IMS hold; the order remains blocked despite the fix |
| `count correction dossier` | Agent output for IMS incidents: entity, hold reason, expected vs. actual quantity, correction steps, and hold-release instruction |
| `count-correction API/UI` | IMS interface through which an engineer corrects inventory counts; the count flows through IMS, not a direct DB UPDATE |
| `IMS count correction` | Process of updating IMS to reflect actual physical inventory after a discrepancy is verified |
| `validated → prioritized` | Order lifecycle transition where IMS can place a hold |
| `picking → picked` | Most common transition where IMS cycle count runs and a hold may be placed |
