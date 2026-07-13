"""Jira poller — deterministic, no LLM (docs/3-agent-design.md §3.2).

Responsibilities:
  1. Poll Jira for new incidents → start read-only background prep (C7).
  2. Watch 'assigned to' field → when populated, cancel prep and spawn orchestrator.
  3. Watch for reassignment → update Case assignee; orchestrator follows via Case object.
  4. Enforce ≤ MAX_CONCURRENT_ORCHESTRATORS concurrent orchestrators.
  5. Reap completed orchestrator tasks each poll cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from support_orchestration.config.base import MAX_CONCURRENT_ORCHESTRATORS
from support_orchestration.glue.jira import JiraClient
from support_orchestration.models import Case, CaseStatus
from support_orchestration.watcher.intake import case_from_jira

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30

OrchestratorFactory = Callable[[Case], Coroutine[Any, Any, None]]


class JiraPoller:
    """
    Deterministic Jira poll loop. No LLM.

    Inject a JiraClient, CaseStore, BackgroundPrepRunner, and an orchestrator
    factory (async callable that drives one incident). All of these accept stubs
    so the poller is fully testable without live connections.
    """

    def __init__(
        self,
        jira_client: JiraClient,
        state_store: Any,                           # CaseStore — avoids circular import
        background_prep: Any | None = None,         # BackgroundPrepRunner
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self._jira = jira_client
        self._state_store = state_store
        self._background_prep = background_prep
        self._orchestrator_factory = orchestrator_factory

        # jira_id → asyncio.Task
        self._active_cases: dict[str, asyncio.Task[Any]] = {}
        self._background_tasks: dict[str, asyncio.Task[Any]] = {}
        # All jira_ids seen at least once (persists across polls even when no task)
        self._seen_jira_ids: set[str] = set()

    # ── Public interface ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main poll loop. Runs indefinitely."""
        logger.info(
            "Watcher started — polling Jira every %ds (cap=%d orchestrators)",
            POLL_INTERVAL_SECONDS,
            MAX_CONCURRENT_ORCHESTRATORS,
        )
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Poll iteration failed; continuing in %ds", POLL_INTERVAL_SECONDS)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def rehydrate(self, cases: list[Case]) -> int:
        """Crash recovery: resume orchestrators for non-terminal cases from the state store.

        Assigned cases respawn an orchestrator immediately (subject to the cap);
        unassigned ones are marked seen so the normal poll loop picks them up
        when 'assigned to' is populated. Returns the number of orchestrators resumed.
        """
        resumed = 0
        for case in cases:
            jira_id = case.jira_ticket_id
            self._seen_jira_ids.add(jira_id)
            if case.assignee_email and jira_id not in self._active_cases:
                self._try_spawn_orchestrator(jira_id, case)
                if jira_id in self._active_cases:
                    resumed += 1
                    logger.info("WATCHER rehydrated %s (status=%s)", jira_id, case.status.value)
        return resumed

    @property
    def active_count(self) -> int:
        return len(self._active_cases)

    @property
    def prep_count(self) -> int:
        return len(self._background_tasks)

    # ── Poll logic ─────────────────────────────────────────────────────────────

    async def _poll_once(self) -> None:
        try:
            incidents = await self._jira.fetch_open_incidents()
        except Exception:
            logger.exception("Failed to fetch incidents from Jira; skipping poll cycle")
            return

        for ticket in incidents:
            jira_id: str = ticket["id"]
            assignee: str | None = ticket.get("assigned_to")

            is_new = jira_id not in self._seen_jira_ids
            in_prep = jira_id in self._background_tasks
            in_orch = jira_id in self._active_cases

            if is_new:
                # ── First time we see this incident ────────────────────────────
                self._seen_jira_ids.add(jira_id)
                case = case_from_jira(ticket)
                case.assignee_email = assignee
                self._state_store.save_case(case)

                if assignee:
                    # Already assigned on first poll: skip background prep
                    case.status = CaseStatus.triaging
                    self._state_store.save_case(case)
                    self._try_spawn_orchestrator(jira_id, case)
                    logger.info(
                        "WATCHER new (pre-assigned) incident %s priority=%s → %s",
                        jira_id, case.priority.value, assignee,
                    )
                else:
                    # Unassigned: start read-only background prep
                    self._start_background_prep(case)
                    logger.info(
                        "WATCHER new (unassigned) incident %s priority=%s",
                        jira_id, case.priority.value,
                    )

            elif not in_orch and assignee:
                # ── Known incident, not yet orchestrated, now has an assignee ─
                if in_prep:
                    bg_task = self._background_tasks.pop(jira_id, None)
                    if bg_task and not bg_task.done():
                        bg_task.cancel()

                case = self._state_store.load_case_by_jira_id(jira_id) or case_from_jira(ticket)
                if case.assignee_email != assignee:
                    case.assignee_email = assignee
                    case.status = CaseStatus.triaging
                    self._state_store.save_case(case)
                    self._try_spawn_orchestrator(jira_id, case)
                    logger.info(
                        "WATCHER incident %s assigned to %s — spawning orchestrator",
                        jira_id, assignee,
                    )

            elif in_orch and assignee:
                # ── Orchestrator running; watch for reassignment ────────────────
                case = self._state_store.load_case_by_jira_id(jira_id)
                if case and case.assignee_email != assignee:
                    old = case.assignee_email
                    case.assignee_email = assignee
                    case.append_trail(
                        actor="watcher",
                        action="reassigned",
                        notes=f"{old} → {assignee}",
                    )
                    self._state_store.save_case(case)
                    logger.info(
                        "WATCHER reassignment %s: %s → %s", jira_id, old, assignee
                    )
                    # The running orchestrator polls case.assignee_email and adapts.

        self._reap_completed()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _start_background_prep(self, case: Case) -> None:
        if self._background_prep is None:
            return
        task = asyncio.create_task(
            self._background_prep.prepare(case),
            name=f"prep-{case.jira_ticket_id}",
        )
        self._background_tasks[case.jira_ticket_id] = task

    def _try_spawn_orchestrator(self, jira_id: str, case: Case) -> None:
        if len(self._active_cases) >= MAX_CONCURRENT_ORCHESTRATORS:
            logger.warning(
                "WATCHER cap reached (%d/%d); %s will be retried next poll",
                len(self._active_cases),
                MAX_CONCURRENT_ORCHESTRATORS,
                jira_id,
            )
            return
        if self._orchestrator_factory is None:
            return
        task = asyncio.create_task(
            self._orchestrator_factory(case),
            name=f"orch-{jira_id}",
        )
        self._active_cases[jira_id] = task

    def _reap_completed(self) -> None:
        done_orch = [jid for jid, t in self._active_cases.items() if t.done()]
        for jid in done_orch:
            task = self._active_cases.pop(jid)
            if not task.cancelled() and (exc := task.exception()):
                logger.error("WATCHER orchestrator %s failed: %s", jid, exc)
            else:
                logger.info("WATCHER orchestrator %s completed", jid)

        done_prep = [jid for jid, t in self._background_tasks.items() if t.done()]
        for jid in done_prep:
            task = self._background_tasks.pop(jid)
            if not task.cancelled() and (exc := task.exception()):
                logger.warning("WATCHER background prep %s failed: %s", jid, exc)
