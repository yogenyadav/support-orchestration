"""Tests for C6 Haiku reply interpretation — c6_interpret_reply()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from support_orchestration.glue.teams import c6_interpret_reply


def _mock_client(response_text: str) -> MagicMock:
    block = MagicMock()
    block.text = response_text
    resp = MagicMock()
    resp.content = [block]
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_c6_returns_reject() -> None:
    client = _mock_client("reject")
    intent = await c6_interpret_reply("no that's wrong", "Is root cause lost_ack?", client)
    assert intent == "reject"


@pytest.mark.asyncio
async def test_c6_returns_affirm() -> None:
    client = _mock_client("affirm")
    intent = await c6_interpret_reply("yes agreed", "Is root cause lost_ack?", client)
    assert intent == "affirm"


@pytest.mark.asyncio
async def test_c6_returns_provide_info() -> None:
    client = _mock_client("provide_info")
    intent = await c6_interpret_reply("the order is at priority 3", "What priority?", client)
    assert intent == "provide_info"


@pytest.mark.asyncio
async def test_c6_unknown_intent_maps_to_other() -> None:
    client = _mock_client("banana")
    intent = await c6_interpret_reply("ok", "Agree?", client)
    assert intent == "other"


@pytest.mark.asyncio
async def test_c6_handles_multiword_response() -> None:
    """Only the first word is used; trailing content is ignored."""
    client = _mock_client("affirm yes of course")
    intent = await c6_interpret_reply("ok sounds right", "Agree?", client)
    assert intent == "affirm"


@pytest.mark.asyncio
async def test_c6_fallback_on_exception_reject() -> None:
    """When Haiku call raises, falls back to regex — 'no' → reject."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=Exception("network error"))
    intent = await c6_interpret_reply("no that is wrong", "Agree?", client)
    assert intent == "reject"


@pytest.mark.asyncio
async def test_c6_fallback_on_exception_affirm() -> None:
    """When Haiku call raises, falls back to regex — 'yes ok' → affirm."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=Exception("timeout"))
    intent = await c6_interpret_reply("yes sounds right", "Agree?", client)
    assert intent == "affirm"
