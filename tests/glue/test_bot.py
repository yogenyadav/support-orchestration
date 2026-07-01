"""Tests for BotFrameworkTransport — Teams proactive DM transport."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from support_orchestration.glue.bot import BotFrameworkTransport, build_from_env
from support_orchestration.glue.teams import TeamsTransport


def _make_transport() -> BotFrameworkTransport:
    return BotFrameworkTransport(
        app_id="app-id-123",
        app_password="app-pw",
        tenant_id="tenant-abc",
    )


def _conv_ref(conversation_id: str = "conv-1") -> str:
    return json.dumps({
        "service_url": "https://smba.trafficmanager.net/teams/",
        "conversation_id": conversation_id,
    })


# ── ABC compliance ────────────────────────────────────────────────────────────

def test_bot_is_teams_transport():
    assert isinstance(_make_transport(), TeamsTransport)


# ── Token acquisition ─────────────────────────────────────────────────────────

def _make_mock_aiohttp_session(token_response: dict | None = None):
    """Build a fully mocked aiohttp module with a session that returns the given response."""
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=token_response or {})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_aiohttp = MagicMock()
    mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
    mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
    return mock_aiohttp, mock_session, mock_resp


@pytest.mark.asyncio
async def test_get_token_success():
    transport = _make_transport()
    mock_aiohttp, _, _ = _make_mock_aiohttp_session(
        {"access_token": "tok123", "expires_in": 3600}
    )

    with patch("support_orchestration.glue.bot.aiohttp", mock_aiohttp):
        token = await transport._get_token()

    assert token == "tok123"
    assert transport._token == "tok123"


@pytest.mark.asyncio
async def test_get_token_cached():
    """Second call returns cached token without HTTP request."""
    transport = _make_transport()
    import time
    transport._token = "cached"
    transport._token_expires_at = time.monotonic() + 7200  # far in the future

    mock_aiohttp = MagicMock()
    with patch("support_orchestration.glue.bot.aiohttp", mock_aiohttp):
        token = await transport._get_token()

    assert token == "cached"
    mock_aiohttp.ClientSession.assert_not_called()


# ── send_message ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_posts_to_connector():
    transport = _make_transport()
    transport._token = "test-token"
    import time
    transport._token_expires_at = time.monotonic() + 3600

    mock_aiohttp, mock_session, _ = _make_mock_aiohttp_session()

    with patch("support_orchestration.glue.bot.aiohttp", mock_aiohttp):
        await transport.send_message(_conv_ref("conv-99"), "/info Order stuck in prioritized")

    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args
    assert "conv-99" in call_kwargs[0][0]  # URL contains conversation_id
    body = call_kwargs[1]["json"]
    assert body["text"] == "/info Order stuck in prioritized"
    assert body["type"] == "message"


# ── receive_message / on_activity ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_receive_message_blocked_until_on_activity():
    transport = _make_transport()
    ref = _conv_ref("conv-recv")

    async def _deliver():
        await asyncio.sleep(0.05)
        transport.on_activity("conv-recv", "Looks correct, approved")

    task = asyncio.create_task(_deliver())
    reply = await transport.receive_message(ref, timeout_seconds=2.0)
    await task

    assert reply == "Looks correct, approved"


@pytest.mark.asyncio
async def test_receive_message_timeout():
    transport = _make_transport()
    with pytest.raises(TimeoutError, match="No reply"):
        await transport.receive_message(_conv_ref("conv-x"), timeout_seconds=0.05)


@pytest.mark.asyncio
async def test_on_activity_delivers_to_queue():
    transport = _make_transport()
    transport.on_activity("conv-q", "hello from engineer")
    ref = json.dumps({"service_url": "https://smba.trafficmanager.net/teams/", "conversation_id": "conv-q"})
    reply = await transport.receive_message(ref, timeout_seconds=0.5)
    assert reply == "hello from engineer"


# ── build_from_env ────────────────────────────────────────────────────────────

def test_build_from_env_raises_without_vars(monkeypatch):
    monkeypatch.delenv("TEAMS_APP_ID", raising=False)
    monkeypatch.delenv("TEAMS_APP_PASSWORD", raising=False)
    monkeypatch.delenv("TEAMS_TENANT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TEAMS_APP_ID"):
        build_from_env()


def test_build_from_env_succeeds_with_vars(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "aid")
    monkeypatch.setenv("TEAMS_APP_PASSWORD", "apw")
    monkeypatch.setenv("TEAMS_TENANT_ID", "tid")
    t = build_from_env()
    assert t._app_id == "aid"
    assert t._tenant_id == "tid"
