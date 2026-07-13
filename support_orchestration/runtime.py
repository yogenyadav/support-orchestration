"""Composition root — wires config → stores → clients → poller → orchestrators.

`python -m support_orchestration` starts the long-running service:

    Watcher (JiraPoller) ──► BackgroundPrepRunner   (unassigned incidents, C7)
            │
            └──► Orchestrator per assigned incident (≤10 concurrent)
                      └──► domain subagent + read-only adapters

Two modes, selected by `mode:` in config/runtime.yaml (or --mode):

  mock        — the PoC mode. Every external system is an in-memory stub;
                nothing leaves the process.
  production  — real clients built from environment variables *named* in the
                config file (secrets never live in the file). Any credential
                left unset degrades to the corresponding stub with a warning,
                so the same code runs live once connectivity is configured.

On startup the runtime rehydrates: every non-terminal Case in the state store
is reloaded and its orchestrator resumed before polling begins (crash recovery).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path
from typing import Any

import yaml

from support_orchestration.glue.jira import AtlassianJiraClient, JiraClient, StubJiraClient
from support_orchestration.glue.teams import DialectManager, StubTransport
from support_orchestration.models import Case
from support_orchestration.orchestrator.orchestrator import Orchestrator
from support_orchestration.storage.audit import AuditStore
from support_orchestration.storage.state_store import CaseStore
from support_orchestration.subagents.base import BaseSubagent, get_subagent
from support_orchestration.tools.mcp_server import (
    StubDbAdapter,
    StubGithubAdapter,
    StubLogAdapter,
    StubPhoenixAdapter,
    StubVectorAdapter,
)
from support_orchestration.watcher.background_prep import BackgroundPrepRunner
from support_orchestration.watcher.jira_poller import JiraPoller

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/runtime.yaml")

# A synthetic pre-assigned incident for `--demo` in mock mode: exercises the
# whole loop (poll → case → orchestrator → dossier → triage → escalate/resolve)
# with zero external connectivity.
DEMO_TICKET: dict[str, Any] = {
    "id": "WH-DEMO-1",
    "client": "example-client",
    "priority": "P2",
    "assigned_to": "engineer@example.com",
    "summary": "Order 12345 stuck at prioritized — no release to picking",
    "background": "Operators report order 12345 has not moved for 45 minutes.",
}


def load_runtime_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load the runtime YAML. A missing file yields mock-mode defaults."""
    if not path.exists():
        logger.warning("Runtime config %s not found — defaulting to mock mode", path)
        return {"mode": "mock"}
    with path.open() as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}
    return config


def _env_from(config: dict[str, Any], section: str, key: str) -> str:
    """Read the env var whose *name* is stored at config[section][key]."""
    var_name: str = (config.get(section) or {}).get(key, "") or ""
    return os.environ.get(var_name, "") if var_name else ""


def _stub_adapters() -> dict[str, Any]:
    return {
        "db": StubDbAdapter(),
        "log": StubLogAdapter(),
        "vector": StubVectorAdapter(),
        "github": StubGithubAdapter(),
        "phoenix": StubPhoenixAdapter(),
    }


def _production_adapters() -> dict[str, Any]:
    """Production adapters from env vars; unset ones fall back to stubs."""
    from support_orchestration.tools.adapters.factory import build_adapters_from_env

    built = build_adapters_from_env()
    stubs = _stub_adapters()
    return {name: built.get(name) or stubs[name] for name in stubs}


class Runtime:
    """Owns every long-lived component and the service lifecycle."""

    def __init__(self, config: dict[str, Any] | None = None, *, demo: bool = False) -> None:
        self.config = config or {"mode": "mock"}
        self.mode: str = self.config.get("mode", "mock")
        if self.mode not in ("mock", "production"):
            raise ValueError(f"Unknown mode {self.mode!r} — expected 'mock' or 'production'")

        stores = self.config.get("stores") or {}
        state_db = Path(stores.get("state_db", "data/state.db"))
        audit_db = Path(stores.get("audit_db", "data/audit.db"))
        state_db.parent.mkdir(parents=True, exist_ok=True)
        audit_db.parent.mkdir(parents=True, exist_ok=True)

        self.state_store = CaseStore(db_path=state_db)
        self.audit_store = AuditStore(db_path=audit_db)

        self.anthropic_client = self._build_anthropic()
        self.jira_client = self._build_jira(demo=demo)
        self.teams_transport = self._build_teams()
        self.adapters = _stub_adapters() if self.mode == "mock" else _production_adapters()

        self.prep_runner = BackgroundPrepRunner(
            state_store=self.state_store,
            anthropic_client=self.anthropic_client,
            vector_adapter=self.adapters["vector"],
        )
        self.poller = JiraPoller(
            self.jira_client,
            self.state_store,
            background_prep=self.prep_runner,
            orchestrator_factory=self._run_case,
        )
        logger.info(
            "Runtime built: mode=%s llm=%s jira=%s teams=%s",
            self.mode,
            "on" if self.anthropic_client else "off (deterministic paths)",
            type(self.jira_client).__name__,
            "on" if self.teams_transport else "off (stub dialect)",
        )

    # ── Component builders ─────────────────────────────────────────────────────

    def _build_anthropic(self) -> Any | None:
        if self.mode == "mock":
            return None
        api_key = _env_from(self.config, "llm", "api_key_env")
        if not api_key:
            logger.warning("LLM api key env unset — LLM steps degrade to deterministic paths")
            return None
        import anthropic

        return anthropic.AsyncAnthropic(api_key=api_key)

    def _build_jira(self, *, demo: bool) -> JiraClient:
        if self.mode == "production":
            base_url = _env_from(self.config, "jira", "base_url_env")
            email = _env_from(self.config, "jira", "email_env")
            token = _env_from(self.config, "jira", "api_token_env")
            if base_url and email and token:
                project_key = (self.config.get("jira") or {}).get("project_key", "WH")
                logger.info("AtlassianJiraClient configured → %s", base_url)
                return AtlassianJiraClient(
                    base_url=base_url, email=email, api_token=token, project_key=project_key
                )
            logger.warning("Jira env vars unset — using StubJiraClient (no incidents will arrive)")
        stub = StubJiraClient()
        if demo:
            stub.add_incident(dict(DEMO_TICKET))
            logger.info("Demo incident %s seeded into stub Jira", DEMO_TICKET["id"])
        return stub

    def _build_teams(self) -> Any | None:  # BotFrameworkTransport | None
        if self.mode == "mock":
            return None
        app_id = _env_from(self.config, "teams", "app_id_env")
        app_password = _env_from(self.config, "teams", "app_password_env")
        tenant_id = _env_from(self.config, "teams", "tenant_id_env")
        if not (app_id and app_password and tenant_id):
            logger.warning("Teams env vars unset — engineer dialogue uses stub transport")
            return None
        from support_orchestration.glue.bot import BotFrameworkTransport

        return BotFrameworkTransport(
            app_id=app_id, app_password=app_password, tenant_id=tenant_id
        )

    # ── Per-incident orchestration ─────────────────────────────────────────────

    async def _build_dialect(self, case: Case) -> DialectManager:
        """Teams DM when transport is configured; stub (in-memory) otherwise."""
        if self.teams_transport is not None and case.assignee_email:
            try:
                ref = await self.teams_transport.open_direct_message(case.assignee_email)
                dialect = DialectManager(self.teams_transport, case)
                dialect.set_conversation_ref(ref)
                return dialect
            except Exception:
                logger.exception(
                    "Failed to open Teams DM with %s — falling back to stub dialect",
                    case.assignee_email,
                )
        dialect = DialectManager(StubTransport(), case)
        dialect.set_conversation_ref(case.assignee_email or case.jira_ticket_id)
        return dialect

    async def _run_case(self, case: Case) -> None:
        """Orchestrator factory handed to the poller — one call per incident."""
        dialect = await self._build_dialect(case)

        def subagent_factory(domain: str, c: Case) -> BaseSubagent:
            return get_subagent(
                domain,
                c,
                anthropic_client=self.anthropic_client,
                dialect=dialect,
                audit_store=self.audit_store,
                adapters=self.adapters,
            )

        orchestrator = Orchestrator(
            case,
            dialect=dialect,
            anthropic_client=self.anthropic_client,
            state_store=self.state_store,
            jira_client=self.jira_client,
            vector_adapter=self.adapters["vector"],
            subagent_factory=subagent_factory,
        )
        try:
            await orchestrator.run()
        except Exception:
            # Orchestrator.run has already escalated + persisted; contain the
            # exception here so the poller task ends cleanly.
            logger.exception("Orchestrator for %s ended with error", case.jira_ticket_id)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def rehydrate(self) -> int:
        """Resume orchestrators for all non-terminal cases in the state store."""
        cases: list[Case] = []
        for jira_id in self.state_store.get_active_jira_ids():
            case = self.state_store.load_case_by_jira_id(jira_id)
            if case is not None:
                cases.append(case)
        resumed = self.poller.rehydrate(cases)
        logger.info("Rehydrated %d case(s); %d orchestrator(s) resumed", len(cases), resumed)
        return resumed

    async def run(self) -> None:
        """Rehydrate, then poll until SIGINT/SIGTERM."""
        self.rehydrate()

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # e.g. Windows event loops
                loop.add_signal_handler(sig, stop.set)

        poll_task = asyncio.create_task(self.poller.run(), name="jira-poller")
        stop_task = asyncio.create_task(stop.wait(), name="stop-signal")
        done, _ = await asyncio.wait({poll_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

        if poll_task in done and not stop.is_set():
            # Poller loop is designed to run forever — reaching here means it crashed.
            poll_task.result()

        logger.info("Shutdown requested — stopping poller")
        poll_task.cancel()
        stop_task.cancel()
        await asyncio.gather(poll_task, stop_task, return_exceptions=True)
        logger.info("Shutdown complete")
