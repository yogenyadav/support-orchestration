"""Tests for log_read — routing across the three connectivity postures."""

import pytest

from support_orchestration.tools.log_reader import is_relay_required, log_read
from support_orchestration.tools.mcp_server import StubLogAdapter


@pytest.mark.asyncio
async def test_direct_posture_returns_content() -> None:
    adapter = StubLogAdapter(fixture="2024-01-01 ERROR wes timeout")
    result = await log_read("acme", "show last 100 lines", "direct", adapter, host="wes-host-01")
    assert result["posture"] == "direct"
    assert "timeout" in result["content"]


@pytest.mark.asyncio
async def test_s3_posture_returns_content() -> None:
    adapter = StubLogAdapter(fixture="INFO order 12345 released")
    result = await log_read("acme", "search for order 12345", "s3", adapter,
                            bucket="acme", prefix="wes/")
    assert result["posture"] == "s3"
    assert "12345" in result["content"]


@pytest.mark.asyncio
async def test_human_relay_posture_returns_sentinel() -> None:
    adapter = StubLogAdapter()
    result = await log_read("acme", "what is the WES log for 14:30?", "human_relay", adapter)
    assert is_relay_required(result)
    assert result["posture"] == "human_relay"
    assert "question" in result


@pytest.mark.asyncio
async def test_direct_posture_requires_host() -> None:
    adapter = StubLogAdapter()
    with pytest.raises(ValueError, match="host"):
        await log_read("acme", "query", "direct", adapter)


@pytest.mark.asyncio
async def test_unknown_posture_raises() -> None:
    adapter = StubLogAdapter()
    with pytest.raises(ValueError, match="unknown log_posture"):
        await log_read("acme", "query", "unknown", adapter)
