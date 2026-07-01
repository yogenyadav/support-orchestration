"""BotFrameworkTransport — real Microsoft Teams transport via Azure Bot Framework.

Implements the TeamsTransport ABC from glue/teams.py using the Bot Framework
REST API for proactive DMs. The StubTransport in glue/teams.py remains the
test double; this class is for the production runtime.

Architecture:
  - Proactive send: OAuth2 client_credentials → Bot Connector API
  - Receive: asyncio.Queue populated by the webhook handler (on_activity)
  - conversation_ref: JSON str with service_url + conversation_id

Requires: aiohttp (for async HTTP), installed as a transitive dep of
botframework-connector. Also install botframework-connector for auth helpers.

Setup:
  1. Register a Bot in Azure → App ID + App Password
  2. Add the bot to your Teams tenant
  3. Set TEAMS_APP_ID, TEAMS_APP_PASSWORD, TEAMS_TENANT_ID env vars
  4. Expose a webhook endpoint at /api/messages; call on_activity() from it
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

from support_orchestration.glue.teams import TeamsTransport

logger = logging.getLogger(__name__)

# Teams Bot Connector REST API
_TEAMS_SERVICE_URL = "https://smba.trafficmanager.net/teams/"
_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_BOT_SCOPE = "https://api.botframework.com/.default"

_DEFAULT_TIMEOUT = 30.0
_TOKEN_REFRESH_BUFFER = 60  # refresh token 60s before expiry


class BotFrameworkTransport(TeamsTransport):
    """
    Real Teams transport using the Azure Bot Framework REST API.

    Proactive messaging works by:
      1. Acquiring an OAuth2 client_credentials token for the Bot Framework scope
      2. POSTing to the Bot Connector's /v3/conversations endpoint to create a
         1:1 conversation with the engineer
      3. POSTing activities to that conversation for each message

    The webhook handler (FastAPI / Azure Function / etc.) must call on_activity()
    when Teams delivers an incoming message — this unblocks receive_message().

    Args:
        app_id:       Azure Bot App ID (from Azure portal).
        app_password: Azure Bot App Password (client secret).
        tenant_id:    Azure AD tenant ID for your organisation.
        service_url:  Teams Bot Connector endpoint (default: global Teams endpoint).
    """

    def __init__(
        self,
        app_id: str,
        app_password: str,
        tenant_id: str,
        service_url: str = _TEAMS_SERVICE_URL,
    ) -> None:
        self._app_id = app_id
        self._app_password = app_password
        self._tenant_id = tenant_id
        self._service_url = service_url.rstrip("/")

        # OAuth token cache: (access_token, expires_at)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

        # Incoming message queues keyed by conversation_id
        self._reply_queues: dict[str, asyncio.Queue[str]] = {}

    # ── OAuth2 token management ───────────────────────────────────────────────

    def _require_aiohttp(self) -> None:
        if aiohttp is None:
            raise RuntimeError(
                "aiohttp is required for BotFrameworkTransport. Install: pip install aiohttp"
            )

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if near-expiry."""
        if self._token and time.monotonic() < self._token_expires_at - _TOKEN_REFRESH_BUFFER:
            return self._token

        self._require_aiohttp()
        token_url = _TOKEN_URL_TEMPLATE.format(tenant_id=self._tenant_id)
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._app_id,
            "client_secret": self._app_password,
            "scope": _BOT_SCOPE,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=payload, timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT)) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()

        self._token = data["access_token"]
        self._token_expires_at = time.monotonic() + data.get("expires_in", 3600)
        return self._token

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ── Conversation management ───────────────────────────────────────────────

    async def open_direct_message(self, teams_user_id: str) -> str:
        """
        Start a proactive 1:1 DM with an engineer by their Teams user ID.

        The Teams user ID is the AAD Object ID (a GUID), not their email. Use
        the Microsoft Graph API to look up the user by email first if needed.

        Returns:
            conversation_ref JSON string for use in send_message / receive_message.
        """
        token = await self._get_token()
        url = f"{self._service_url}/v3/conversations"
        body = {
            "isGroup": False,
            "bot": {"id": self._app_id, "name": "SupportAgent"},
            "members": [{"id": teams_user_id}],
            "channelData": {"tenant": {"id": self._tenant_id}},
        }

        self._require_aiohttp()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=self._auth_headers(token),
                json=body,
                timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        conversation_id = data["id"]
        ref = {"service_url": self._service_url, "conversation_id": conversation_id}
        logger.info("BOT_DM_OPENED conversation_id=%s teams_user=%s", conversation_id, teams_user_id)
        return json.dumps(ref)

    # ── TeamsTransport ABC ────────────────────────────────────────────────────

    async def send_message(self, conversation_ref: str, message: str) -> None:
        """Send a text message to the conversation identified by conversation_ref."""
        ref = json.loads(conversation_ref)
        service_url = ref.get("service_url", self._service_url)
        conversation_id = ref["conversation_id"]

        token = await self._get_token()
        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        body = {
            "type": "message",
            "from": {"id": self._app_id},
            "conversation": {"id": conversation_id},
            "text": message,
        }

        self._require_aiohttp()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=self._auth_headers(token),
                json=body,
                timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()

        logger.info(
            "BOT_SEND conversation_id=%s len=%d", conversation_id, len(message),
        )

    async def receive_message(
        self,
        conversation_ref: str,
        timeout_seconds: float = 300,
    ) -> str:
        """
        Wait for the next message from the engineer in this conversation.

        Blocks until on_activity() delivers a message or timeout_seconds elapse.
        Raises TimeoutError on timeout (caller should escalate or re-ask).
        """
        ref = json.loads(conversation_ref)
        conversation_id = ref["conversation_id"]

        if conversation_id not in self._reply_queues:
            self._reply_queues[conversation_id] = asyncio.Queue()

        try:
            reply = await asyncio.wait_for(
                self._reply_queues[conversation_id].get(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"No reply from engineer in conversation {conversation_id} "
                f"after {timeout_seconds:.0f}s"
            )

        logger.info(
            "BOT_RECV conversation_id=%s len=%d", conversation_id, len(reply),
        )
        return reply

    def on_activity(self, conversation_id: str, text: str) -> None:
        """
        Called by the webhook handler when Teams delivers an incoming message.

        The webhook handler parses the Activity JSON and calls this method with
        the conversation ID and message text. This unblocks receive_message().

        Thread-safe: can be called from a sync web framework handler.
        """
        if conversation_id not in self._reply_queues:
            self._reply_queues[conversation_id] = asyncio.Queue()

        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(self._reply_queues[conversation_id].put_nowait, text)
        else:
            self._reply_queues[conversation_id].put_nowait(text)

        logger.info("BOT_ACTIVITY_RECEIVED conversation_id=%s len=%d", conversation_id, len(text))


def build_from_env() -> BotFrameworkTransport:
    """Build a BotFrameworkTransport from environment variables."""
    import os
    app_id = os.environ.get("TEAMS_APP_ID", "")
    app_password = os.environ.get("TEAMS_APP_PASSWORD", "")
    tenant_id = os.environ.get("TEAMS_TENANT_ID", "")
    if not all([app_id, app_password, tenant_id]):
        raise RuntimeError(
            "TEAMS_APP_ID, TEAMS_APP_PASSWORD, and TEAMS_TENANT_ID env vars are required"
        )
    return BotFrameworkTransport(app_id=app_id, app_password=app_password, tenant_id=tenant_id)
