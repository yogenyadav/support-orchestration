"""Base domain subagent — shared diagnosis loop, hook wiring, and output contract.

Domain roster (priority order, highest to lowest):
  WES         — Orchestration Engine (pages most → first subagent built in Prompt 7)
  GTP_PICKING — GTP Picking (Good-to-Pick station picking)
  GTP_DECANT  — GTP Decant (Good-to-Pick station decant)
  IMS         — Inventory Management System
  ASRS        — Automated Storage and Retrieval System
  LPN         — Label/Printer (License Plate Number printing)
  WCS         — Warehouse Control System (lowest priority)
  infra       — Infrastructure (VM/hypervisor/OS layer; cross-cutting)
  ESB         — Enterprise Service Bus / ActiveMQ+Camel

Prompt 7: BaseSubagent.diagnose() is implemented using the raw Messages API tool loop.
WES is the first domain with a working diagnose(); all others inherit it.
"""

from __future__ import annotations

import logging
from typing import Any

from support_orchestration.config.base import MODEL_SONNET
from support_orchestration.models import Case, Diagnosis
from support_orchestration.tools.hooks import ALLOWED_TOOLS

logger = logging.getLogger(__name__)

MAX_TURNS = 12   # hard cap; SLA-aware exit would trigger earlier in practice


class BaseSubagent:
    """
    Shared base for all domain subagents.

    Subclasses set:
      DOMAIN: str — canonical domain key (used in Case.owning_domain and lifecycle maps)

    Constructor accepts injectable dependencies for testability:
      anthropic_client — raw anthropic.AsyncAnthropic; None → diagnose() raises NotImplementedError
      dialect          — DialectManager for human-relay /ask turns inside the tool loop
      audit_store      — AuditStore for PostToolUse audit writes
      adapters         — dict[str, Adapter] from eval fixtures or production connections
    """

    DOMAIN: str = "base"

    def __init__(
        self,
        case: Case,
        *,
        anthropic_client: Any = None,
        dialect: Any = None,
        audit_store: Any = None,
        adapters: dict[str, Any] | None = None,
    ) -> None:
        self.case = case
        self._anthropic = anthropic_client
        self._dialect = dialect
        self._audit_store = audit_store
        self._adapters: dict[str, Any] = adapters or {}

    @property
    def system_prompt(self) -> str:
        raise NotImplementedError

    @property
    def domain_allowed_tools(self) -> frozenset[str]:
        return ALLOWED_TOOLS

    # ── Main diagnosis loop ────────────────────────────────────────────────────

    async def diagnose(self) -> Diagnosis:
        """
        Run the raw Messages API diagnosis loop and return structured output.

        Uses Sonnet 4.6 (C4) with the domain system prompt + lifecycle context.
        PreToolUse hooks enforce write-block / client-scope / allowlist each turn.
        PostToolUse hook audits every read.

        Raises NotImplementedError when anthropic_client is None (test/offline mode).
        """
        if self._anthropic is None:
            raise NotImplementedError(
                f"{self.DOMAIN} subagent requires anthropic_client for diagnose(). "
                "Pass anthropic_client= to get_subagent() or inject a mock in tests."
            )

        from support_orchestration.subagents.prompts import (
            DIAGNOSIS_TOOL_SCHEMAS,
            bounded_give_up,
            parse_diagnosis_json,
            render_case_for_diagnosis,
        )
        from support_orchestration.tools.mcp_server import make_agent_hooks

        hooks = make_agent_hooks(self.case, self._audit_store)
        pre_hook = hooks["PreToolUse"]
        post_hook = hooks["PostToolUse"]

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": render_case_for_diagnosis(self.case)}
        ]

        for turn in range(MAX_TURNS):
            logger.debug(
                "Subagent %s turn %d/%d for %s",
                self.DOMAIN, turn + 1, MAX_TURNS, self.case.jira_ticket_id,
            )
            resp = await self._anthropic.messages.create(
                model=MODEL_SONNET,
                max_tokens=2048,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=DIAGNOSIS_TOOL_SCHEMAS,
            )

            # Extend history with the assistant turn
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "end_turn":
                for block in reversed(resp.content):
                    if getattr(block, "type", None) == "text" and block.text:
                        diagnosis = parse_diagnosis_json(block.text, self.case)
                        if diagnosis is not None:
                            logger.info(
                                "Subagent %s diagnosed %s: %s",
                                self.DOMAIN, self.case.jira_ticket_id, diagnosis.next_action,
                            )
                            return diagnosis
                logger.warning(
                    "Subagent %s: end_turn with no parseable diagnosis for %s",
                    self.DOMAIN, self.case.jira_ticket_id,
                )
                return bounded_give_up(self.case, self.DOMAIN)

            if resp.stop_reason == "tool_use":
                tool_results: list[dict[str, Any]] = []
                for block in resp.content:
                    if getattr(block, "type", None) == "tool_use":
                        result = await self._call_tool_with_hooks(
                            block.id, block.name, block.input, pre_hook, post_hook,
                        )
                        tool_results.append(result)
                messages.append({"role": "user", "content": tool_results})
            else:
                logger.warning(
                    "Subagent %s: unexpected stop_reason=%s on turn %d",
                    self.DOMAIN, resp.stop_reason, turn + 1,
                )
                break

        logger.warning(
            "Subagent %s hit MAX_TURNS=%d for %s — bounded give-up",
            self.DOMAIN, MAX_TURNS, self.case.jira_ticket_id,
        )
        from support_orchestration.subagents.prompts import bounded_give_up
        return bounded_give_up(self.case, self.DOMAIN)

    # ── Tool dispatch helpers ──────────────────────────────────────────────────

    async def _call_tool_with_hooks(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        pre_hook: Any,
        post_hook: Any,
    ) -> dict[str, Any]:
        """
        Pre-hook → dispatch → relay-check → post-hook → return tool_result block.
        On PermissionError from pre-hook: returns an is_error tool_result block.
        On relay_required sentinel: sends /ask via dialect and uses engineer reply.
        """
        try:
            pre_hook(tool_name, tool_input)
        except PermissionError as exc:
            logger.warning("Guardrail blocked %s: %s", tool_name, exc)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"BLOCKED: {exc}",
                "is_error": True,
            }

        try:
            result = await self._dispatch_tool(tool_name, tool_input)

            # Human-relay sentinel handling
            if isinstance(result, dict) and result.get("relay_required"):
                result = await self._handle_relay(result)

            post_hook(tool_name, tool_input, result)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": str(result),
            }
        except Exception as exc:
            logger.warning("Tool %s error: %s", tool_name, exc)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Tool error: {exc}",
                "is_error": True,
            }

    async def _dispatch_tool(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        """
        Call the underlying tool function for the given MCP tool name.

        Uses self._adapters when available (eval fixture adapters or production connections),
        falling back to stub adapters for local/offline use.
        """
        from support_orchestration.tools.mcp_server import (
            StubDbAdapter, StubGithubAdapter, StubLogAdapter,
            StubPhoenixAdapter, StubVectorAdapter,
        )

        if tool_name == "mcp__support__db_state_read":
            from support_orchestration.tools.db_state_reader import db_state_read
            db = self._adapters.get("db") or StubDbAdapter()
            return await db_state_read(
                client_id=tool_input["client_id"],
                entity_type=tool_input["entity_type"],
                entity_id=tool_input["entity_id"],
                db=db,
                table_hint=tool_input.get("table_hint"),
            )

        if tool_name == "mcp__support__log_read":
            from support_orchestration.tools.log_reader import log_read
            log = self._adapters.get("log") or StubLogAdapter()
            return await log_read(
                client_id=tool_input["client_id"],
                query=tool_input["query"],
                log_posture=tool_input["log_posture"],
                log=log,
                host=tool_input.get("host"),
                bucket=tool_input.get("bucket"),
                prefix=tool_input.get("prefix"),
            )

        if tool_name == "mcp__support__github_read":
            from support_orchestration.tools.github_reader import github_read
            gh = self._adapters.get("github") or StubGithubAdapter()
            return await github_read(
                client_id=tool_input["client_id"],
                path=tool_input["path"],
                repo=tool_input.get("repo"),
                ref=tool_input.get("ref", "main"),
                org=tool_input.get("org"),
                github=gh,
            )

        if tool_name == "mcp__support__history_search":
            from support_orchestration.tools.history_retrieval import history_search
            vec = self._adapters.get("vector") or StubVectorAdapter()
            return await history_search(
                client_id=tool_input["client_id"],
                query=tool_input["query"],
                vector=vec,
                top_k=tool_input.get("top_k", 5),
                entity_type=tool_input.get("entity_type"),
                domain=tool_input.get("domain"),
            )

        if tool_name == "mcp__support__phoenix_resolve":
            phx = self._adapters.get("phoenix")
            if phx is not None:
                return await phx.resolve(tool_input["client_id"])
            from support_orchestration.tools.phoenix_resolver import phoenix_resolve
            return await phoenix_resolve(
                client_id=tool_input["client_id"],
                force_refresh=tool_input.get("force_refresh", False),
            )

        raise ValueError(
            f"Unknown tool '{tool_name}'. Allowed: {sorted(ALLOWED_TOOLS)}"
        )

    async def _handle_relay(self, sentinel: dict[str, Any]) -> dict[str, Any]:
        """
        Handle a human-relay sentinel from log_read.

        If a DialectManager is available: sends /ask and waits for the engineer's reply.
        Otherwise: returns an informational result so the agent can request need_info.
        """
        question = sentinel.get("question", "Please provide the requested log information.")
        if self._dialect is not None:
            logger.info(
                "Subagent %s: relay /ask for %s", self.DOMAIN, self.case.jira_ticket_id,
            )
            await self._dialect.send("/ask", question)
            reply = await self._dialect.receive()
            return {"relayed_answer": reply, "question": question}

        return {
            "relay_required": True,
            "question": question,
            "note": "No dialect available — agent should return need_info.",
        }

    def _is_mine(self, case: Case) -> bool:
        return case.owning_domain == self.DOMAIN


# ── Domain subagents (priority order) ────────────────────────────────────────

class WESSubagent(BaseSubagent):
    """Orchestration Engine — highest incident volume; first subagent built (Prompt 7)."""
    DOMAIN = "WES"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_wes_system_prompt
        return build_wes_system_prompt(self.case.client)


class GTPPickingSubagent(BaseSubagent):
    """GTP Picking — Good-to-Pick station picking operations."""
    DOMAIN = "GTP_PICKING"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_gtp_picking_system_prompt
        return build_gtp_picking_system_prompt(self.case.client)


class GTPDecantSubagent(BaseSubagent):
    """GTP Decant — Good-to-Pick station decant operations."""
    DOMAIN = "GTP_DECANT"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_gtp_decant_system_prompt
        return build_gtp_decant_system_prompt(self.case.client)


class IMSSubagent(BaseSubagent):
    """Inventory Management System — cycle counts, holds, discrepancies."""
    DOMAIN = "IMS"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_ims_system_prompt
        return build_ims_system_prompt(self.case.client)


class ASRSSubagent(BaseSubagent):
    """Automated Storage and Retrieval System — bin storage, retrieval, robot/crane operations."""
    DOMAIN = "ASRS"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_asrs_system_prompt
        return build_asrs_system_prompt(self.case.client)


class LPNSubagent(BaseSubagent):
    """LPN/Printer — License Plate Number label printing and print-and-apply operations."""
    DOMAIN = "LPN"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_lpn_system_prompt
        return build_lpn_system_prompt(self.case.client)


class WCSSubagent(BaseSubagent):
    """Warehouse Control System — conveyor, sorter, divert hardware. Lowest incident priority."""
    DOMAIN = "WCS"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_wcs_system_prompt
        return build_wcs_system_prompt(self.case.client)


class InfraSubagent(BaseSubagent):
    """Infrastructure — VM, hypervisor, OS, disk, OOM. Cross-cutting across domains."""
    DOMAIN = "infra"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_infra_system_prompt
        return build_infra_system_prompt(self.case.client)


class ESBSubagent(BaseSubagent):
    """ESB — ActiveMQ + Apache Camel message routing layer."""
    DOMAIN = "ESB"

    @property
    def system_prompt(self) -> str:
        from support_orchestration.subagents.prompts import build_esb_system_prompt
        return build_esb_system_prompt(self.case.client)


_BASE_CONTEXT = (
    "You are a domain subagent for a warehouse automation support system. "
    "Diagnose why an entity is stuck in the lifecycle using the read-only tools. "
    "Output your conclusion as a <diagnosis>…</diagnosis> JSON block.\n\n"
)


# ── Registry ──────────────────────────────────────────────────────────────────

DOMAIN_PRIORITY_ORDER: list[str] = [
    "WES",
    "GTP_PICKING",
    "GTP_DECANT",
    "IMS",
    "ASRS",
    "LPN",
    "WCS",
    "infra",
    "ESB",
]

DOMAIN_SUBAGENT_MAP: dict[str, type[BaseSubagent]] = {
    "WES":         WESSubagent,
    "GTP_PICKING": GTPPickingSubagent,
    "GTP_DECANT":  GTPDecantSubagent,
    "IMS":         IMSSubagent,
    "ASRS":        ASRSSubagent,
    "LPN":         LPNSubagent,
    "WCS":         WCSSubagent,
    "infra":       InfraSubagent,
    "ESB":         ESBSubagent,
}


def get_subagent(
    domain: str,
    case: Case,
    *,
    anthropic_client: Any = None,
    dialect: Any = None,
    audit_store: Any = None,
    adapters: dict[str, Any] | None = None,
) -> BaseSubagent:
    """
    Factory: return the subagent for the given domain, with deps injected.

    anthropic_client: required for diagnose(); omit for tests that use stub factories.
    adapters: fixture adapters (eval harness) or production adapters (runtime).
    """
    cls = DOMAIN_SUBAGENT_MAP.get(domain)
    if cls is None:
        raise ValueError(
            f"Unknown domain '{domain}'. Valid domains: {DOMAIN_PRIORITY_ORDER}"
        )
    return cls(
        case,
        anthropic_client=anthropic_client,
        dialect=dialect,
        audit_store=audit_store,
        adapters=adapters,
    )
