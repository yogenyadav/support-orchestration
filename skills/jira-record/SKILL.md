# Skill: jira-record

How and when to write the resolution record to Jira, and what it must contain.

## Trigger

Write to Jira exactly once, immediately after the human's `/approve` is received. The `/approve` is the hard gate — do not write to Jira before it.

## What to write

The Jira resolution record must capture everything a future engineer needs to diagnose the same issue faster:

```
Resolution summary
──────────────────
Entity:         {entity_type} {entity_id}
Stuck at:       {from_state} → {to_state}  (owning domain: {domain})
Root cause:     {specific, falsifiable root cause statement}
Blocker class:  {blocker_class from the lifecycle map}

Evidence used:
  - {evidence source 1}: {what it showed}
  - {evidence source 2}: {what it showed}

Fix applied:
  SQL / command: {exact statement the human ran}
  Applied by:    {engineer name, from Jira assignee}
  Applied at:    {ISO 8601 timestamp}

Verification:
  Observed:      {what the engineer confirmed post-fix}
  Time to clear: {time from fix application to entity advancing}

IMS hold:       {yes — cleared at {time} / no}
Connectivity:   {direct_connect / s3_logs / human_relay}
```

## Fields to populate in Jira

Call `jira.write_resolution()` with:

```python
{
    "ticket_id":       case.jira_ticket_id,
    "resolution":      "Fixed",
    "root_cause":      root_cause_string,
    "fix_summary":     fix_summary_string,
    "resolution_note": full_formatted_record_above,
    "resolved_at":     ISO8601_timestamp
}
```

The `Assigned To` field is not changed — that stays with the engineer who approved.

## Quality rules

1. **Specific, not generic.** "WES consumer thread died at 14:32" not "WES was down."
2. **Entity ID always included.** Future history searches match on entity IDs.
3. **Exact SQL always included.** Even if the fix was a restart, record the exact command.
4. **IMS hold status always stated.** This is a major false-positive source — recording it helps future triage.
5. **Blocker class always named.** Powers the `history_search` retrieval for future incidents.
6. **Time to clear recorded.** Used for SLA attainment tracking.

## /approve mirroring rule

The `/approve` message from the engineer is automatically mirrored to Jira as an internal comment with the engineer's identity. The `write_resolution()` call adds the full structured record. Both together form the audit trail.

## After writing

1. Confirm the write succeeded (check the API response).
2. Update the Case object status to `resolved`.
3. Send `/info` to the engineer in Teams: "Resolution recorded in Jira. Case closed."
4. The orchestrator then marks the orchestrator instance done and releases the slot.

## What NOT to include

- Client production data (no raw DB values beyond entity state strings)
- Engineer personal notes or chat transcript
- Speculation — only what was confirmed by evidence
- Passwords, connection strings, or Phoenix details
