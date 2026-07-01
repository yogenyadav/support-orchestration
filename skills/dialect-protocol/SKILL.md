# Skill: dialect-protocol

Agent ↔ engineer communication protocol (docs/3-agent-design.md §3.7).

## Core rules

### One open request at a time
The agent sends exactly one prefixed message and waits for a reply before sending the next.
This makes reply-matching unambiguous and lets the engineer respond naturally.

### Agent → engineer: always prefixed
| Verb        | Use for |
|-------------|---------|
| `/info`     | Sharing facts; no reply expected |
| `/ask`      | Requesting a specific data point (human-relay posture) |
| `/question` | Clarifying ambiguity before proposing a fix |
| `/clarify`  | Asking the engineer to confirm a specific assumption |
| `/validate` | Presenting root cause for lightweight engineer confirmation |
| `/approve`  | Requesting approval of the proposed fix — AUDITED, mirrored to Jira |
| `/status`   | Status update; fire-and-forget |

### Engineer → agent: free-form
The engineer writes natural language. No syntax required. The system classifies intent.

### /ask pattern (human-relay)
When the client has no direct access (`human_relay` posture), the agent requests
specific data points one at a time:

```
Agent:    /ask What is the current state of order 12345 in the orders table?
Engineer: still prioritized
Agent:    /ask Is there an IMS count-hold on order 12345?
Engineer: no hold
```

### /validate before /approve
The agent presents its conclusion for lightweight validation before requesting approval.
If the engineer rejects, the agent escalates with their feedback.

### /approve — the fix gate
/approve is the hard human gate before the fix is recorded. The engineer must explicitly
approve before any fix is applied. The approval is mirrored to Jira as the audit record.

The **human always applies the fix**. The agent never writes to a client system.
