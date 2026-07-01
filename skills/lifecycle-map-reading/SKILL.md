# Skill: lifecycle-map-reading

How to use the lifecycle-to-domain maps (`maps/base/order.yaml`, `maps/base/tote.yaml`) to find a stuck transition and its owning domain.

## What the map gives you

For each stuck entity, the map provides:
- **owning_domain** — which subagent diagnoses this transition
- **trigger** and **trigger_transport** — what should fire and over which transport
- **ims_check_first** — if true, check IMS hold before any other hypothesis
- **candidate_blockers** — ordered checklist of what to rule out
- **dependency_edges** — other entities that must be in a specific state first

The map stores **logical** state names. DB column names and state string values vary per client — discover them at runtime from GitHub code or `information_schema`.

## Step 1 — Identify entity type and current state

From the Jira ticket or the engineer's description, extract:
- `entity_type`: `order` or `tote` (usually `order` for the reported symptom)
- `entity_current_state`: the logical state where it is stuck

If not directly stated, read `db_state_read` to confirm the actual state.

## Step 2 — Find the stuck transition

Open `maps/base/order.yaml` or `maps/base/tote.yaml`. Find the transition where:
- `from` == `entity_current_state`

That transition entry is the unit of diagnosis.

## Step 3 — Check ims_check_first

If `ims_check_first: true`, stop. Verify IMS hold status via `db_state_read` before any other work:
```
db_state_read(client_id="{client}", entity_type="order", entity_id="{id}")
→ check: ims_hold_active, count_hold_reason
```
If a hold is present → reroute to IMS. Do not proceed with domain diagnosis.

## Step 4 — Read owning_domain

Route to the subagent named in `owning_domain` for this transition:

| owning_domain | Subagent |
|---|---|
| ESB | esb-diagnosis |
| WES | wes-diagnosis |
| GTP_PICKING | gtp-picking-diagnosis |
| GTP_DECANT | gtp-decant-diagnosis |
| IMS | ims-diagnosis |
| ASRS | asrs-diagnosis |
| WCS | wcs-diagnosis |
| infra | infra-diagnosis |
| LPN | lpn-diagnosis |

## Step 5 — Load the blocker checklist

Take `candidate_blockers` in `check_order` sequence. This is your evidence-gathering agenda — work it top-to-bottom, eliminating blockers as you gather evidence.

## Step 6 — Check dependency edges

Look at `dependency_edges`. If the transition depends on another entity being in a required state, verify that entity first. A stuck `bin` may be the real root cause of a stuck order.

## Client overlays

Base maps cover all clients. If the client has a custom lifecycle overlay:
- Check `maps/{client_id}/` for deltas
- Client overlays add or replace transitions; base transitions not overridden remain in effect
- The override key is the transition `id` field

## When the entity's state doesn't match any map entry

1. The state name may be a client-specific variant — check client overlay
2. The DB state may use a different string than the logical name — read GitHub code for the state enum
3. The entity may be in an undocumented terminal state — verify with `github_read` on the service code
4. Surface as `need_info` with the exact unknown to resolve
