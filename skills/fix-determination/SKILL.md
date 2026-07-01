# Skill: fix-determination

How to determine a fix once root cause is established, and how to format it for human review.

## When to determine a fix

Only when all three conditions are met:
1. Root cause is specific and supported by direct evidence
2. The exact entity (table, row) and target state are known
3. A verification step exists

Do not propose a fix based on inference alone. If evidence is incomplete, issue `need_info` with the exact missing data point.

## The canonical fix structure

The vast majority of fixes are a targeted DB `UPDATE`. Format the fix proposal as:

```
Root cause:  {specific, falsifiable statement — "WES consumer thread died at 14:32; 
              no ack listener running; 4 orders are stuck in 'released'"}

Entity:      {entity_type} {entity_id} in state '{current_state}'
             Table: {table_name} (discovered from GitHub code)
             Row:   WHERE {pk_column} = '{entity_id}'

Fix:         UPDATE {table_name}
             SET {state_column} = '{target_state}'
             WHERE {pk_column} = '{entity_id}'

             If batch: WHERE {pk_column} IN ({id_list}) — scope to affected IDs only.

Reversible:  {yes — can be rolled back by SET state = '{prior_state}' / no — explain why}

Verification: After applying: {what the engineer observes within what timeframe}
              e.g., "order 12345 reaches 'shipped' within 2 minutes in the orders table"
```

## Fix types

### DB state correction (most common)
Move stuck entity to a terminal or restart state. See canonical structure above.

### Service restart
When root cause is a dead service, not a stuck entity state:
```
Fix:  Restart {service_name} on {host}
      Command: {exact restart command if known from GitHub}
Verification: {N} orders begin advancing within 2 minutes of restart.
```
Note: human applies the restart. The agent never issues a remote command.

### IMS count correction
When IMS hold is the root cause:
```
Fix:  IMS count correction required for order {id}.
      Route to IMS subagent to determine the count discrepancy and correction method.
      Do not advance the order state until IMS confirms count cleared.
```

### Batch fix
When multiple entities are stuck in the same failed transition due to the same root cause:
```
Scope: {N} orders stuck in '{current_state}' since {timestamp}
       IDs: {list — or: WHERE {state_column} = '{current_state}' AND {timestamp_column} < '{cutoff}'}
Fix:   UPDATE ... WHERE {pk_column} IN ({comma-separated IDs})
       Apply in one transaction. Verify all IDs advance within 5 minutes.
```

## Fix quality checklist

Before issuing `/validate`:
- [ ] Table and row are named specifically, not described generally
- [ ] Target state string matches what the code expects (verified via `github_read`)
- [ ] `WHERE` clause is scoped tightly (no `UPDATE table SET state = ... WHERE 1=1`)
- [ ] Reversibility is assessed and stated
- [ ] Verification step is concrete and time-bounded
- [ ] IMS holds have been ruled out (or IMS is the fix)
- [ ] No write to a client system is implied — the human applies this

## Termination outcomes

| Outcome | When |
|---|---|
| `propose_to_human` | Root cause confirmed + fix proposal complete + ready for /validate |
| `need_info` | One specific data point is blocking progress — state it precisely |
| `reroute:{DOMAIN}` | Evidence clearly points to a different domain — name the domain and the evidence |
| `escalate` | Exhausted all available tools and evidence is insufficient to confirm root cause |
| `bounded_give_up` | `max_turns` reached without resolution — hand off to engineer with a partial dossier |

## /validate before /approve

Always issue `/validate` before `/approve`:
```
/validate Root cause confirmed: {brief restatement}. Fix: {one-line summary}. 
Does this match your understanding?
```
If the engineer rejects, incorporate their feedback and either refine or escalate. Only proceed to `/approve` after validation is accepted.
