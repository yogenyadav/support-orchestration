"""Teams dialect transport — agent ↔ engineer interaction over Teams DM.

Dialect rules (docs/3-agent-design.md §3.7):
  - Agent → human: always prefixed verb: /info /ask /validate /approve /status
  - ONE open request at a time (enforced here)
  - Human → human: free-form natural language; no syntax required
  - /approve outcomes mirrored to Jira

C6 — Haiku reply interpretation (docs/4 §4.2):
  c6_interpret_reply() classifies the engineer's free-form reply into
  affirm | reject | provide_info | question | other.
  Falls back to regex if Haiku call fails.

Transport is stubbed behind an interface so dialect logic is testable
without a live Azure Bot. Real transport wired in Prompt 8.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from support_orchestration.config.base import MODEL_HAIKU
from support_orchestration.models import Case, DialogueTurn

logger = logging.getLogger(__name__)

# ── C6 reply classification ───────────────────────────────────────────────────

_VALID_INTENTS = frozenset({"affirm", "reject", "provide_info", "question", "other"})

_C6_CLASSIFY_PROMPT = """\
The support agent sent this request to the engineer:
"{context}"

The engineer replied:
"{reply}"

Classify the engineer's intent with exactly one word:
- affirm      — agreement, yes, ok, confirmed, approved
- reject      — disagreement, no, wrong, incorrect, denied
- provide_info — answering with facts or data
- question    — asking a clarifying question back
- other       — unclear

Reply with ONLY the single classification word."""

_REJECTION_RE = re.compile(r"^(no|nope|disagree|wrong|incorrect)\b", re.IGNORECASE)


async def c6_interpret_reply(
    reply: str,
    context: str,
    anthropic_client: Any,
) -> str:
    """
    C6 Haiku: classify the engineer's free-form reply intent.

    Returns one of: affirm | reject | provide_info | question | other.
    Falls back to simple regex on any exception.
    """
    try:
        resp = await anthropic_client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": _C6_CLASSIFY_PROMPT.format(
                    context=context[:300], reply=reply[:300],
                ),
            }],
        )
        raw = resp.content[0].text.strip().lower().split()[0] if resp.content else "other"
        return raw if raw in _VALID_INTENTS else "other"
    except Exception as exc:
        logger.warning("C6 Haiku failed (%s); falling back to regex", exc)
        return "reject" if _REJECTION_RE.match(reply.strip()) else "affirm"

VALID_AGENT_VERBS = frozenset(
    {"/info", "/ask", "/question", "/clarify", "/validate", "/approve", "/status"}
)


class TeamsTransport(ABC):
    """Interface for the Teams transport. Swap in StubTransport for tests."""

    @abstractmethod
    async def send_message(self, conversation_ref: str, message: str) -> None:
        ...

    @abstractmethod
    async def receive_message(self, conversation_ref: str, timeout_seconds: float) -> str:
        ...


class StubTransport(TeamsTransport):
    """In-memory transport for tests — pre-load replies, capture sent messages."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = list(replies or [])
        self.sent: list[str] = []

    async def send_message(self, conversation_ref: str, message: str) -> None:
        self.sent.append(message)

    async def receive_message(self, conversation_ref: str, timeout_seconds: float = 300) -> str:
        if not self._replies:
            raise TimeoutError("StubTransport: no more replies queued")
        return self._replies.pop(0)


class DialectManager:
    """
    Manages the agent ↔ engineer dialogue: enforces one-open-request-at-a-time,
    prefixes agent messages with verbs, and mirrors /approve to Jira.

    Full implementation in Prompt 7.
    """

    def __init__(self, transport: TeamsTransport, case: Case) -> None:
        self._transport = transport
        self._case = case
        self._conversation_ref: str | None = None

    def set_conversation_ref(self, ref: str) -> None:
        self._conversation_ref = ref

    async def send(self, verb: str, message: str) -> None:
        if verb not in VALID_AGENT_VERBS:
            raise ValueError(
                f"Invalid dialect verb '{verb}'. Must be one of {sorted(VALID_AGENT_VERBS)}"
            )
        if self._case.open_request is not None:
            raise RuntimeError(
                f"Dialect violation: cannot send '{verb}' — open request already pending: "
                f"'{self._case.open_request}'. Wait for human reply."
            )
        full_message = f"{verb} {message}"
        self._case.open_request = full_message
        self._case.dialogue.append(DialogueTurn(
            direction="agent_to_human", verb=verb, message=message,
        ))
        assert self._conversation_ref, "conversation_ref not set"
        await self._transport.send_message(self._conversation_ref, full_message)
        logger.info(
            "DIALECT_SEND %s %s → %s", verb, self._case.jira_ticket_id, self._case.assignee_email
        )

    async def receive(self, timeout_seconds: float = 300) -> str:
        assert self._conversation_ref, "conversation_ref not set"
        reply = await self._transport.receive_message(self._conversation_ref, timeout_seconds)
        self._case.open_request = None
        self._case.dialogue.append(DialogueTurn(direction="human_to_agent", message=reply))
        logger.info(
            "DIALECT_RECV %s ← %s: %s",
            self._case.jira_ticket_id, self._case.assignee_email, reply[:80],
        )
        return reply

    async def send_info(self, message: str) -> None:
        """Send a /status informational message — fire-and-forget, no reply expected.

        Does NOT set ``case.open_request`` so subsequent sends are not blocked.
        Used for the warm-start dossier (docs/3 §3.8).
        """
        full_message = f"/status {message}"
        self._case.dialogue.append(DialogueTurn(
            direction="agent_to_human", verb="/status", message=message,
        ))
        if self._conversation_ref:
            await self._transport.send_message(self._conversation_ref, full_message)
        logger.info(
            "DIALECT_INFO %s → %s", self._case.jira_ticket_id, self._case.assignee_email,
        )

    async def approve(self, message: str) -> str:
        """Send /approve and mirror outcome to Jira when engineer responds."""
        await self.send("/approve", message)
        reply = await self.receive()
        # TODO(P7/P8): mirror approval to Jira
        self._case.fix_approved = True
        return reply
