"""Tests for HttpPhoenixAdapter — production Phoenix HTTP client."""

from __future__ import annotations

import pytest

from support_orchestration.tools.adapters.phoenix_adapter import HttpPhoenixAdapter
from support_orchestration.tools.mcp_server import PhoenixAdapter


def _make_adapter(**kwargs) -> HttpPhoenixAdapter:
    return HttpPhoenixAdapter(
        base_url="https://phoenix.internal",
        api_token="token-xyz",
        **kwargs,
    )


def test_http_phoenix_is_phoenix_adapter():
    assert isinstance(_make_adapter(), PhoenixAdapter)


@pytest.mark.asyncio
async def test_resolve_success():
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "connectivity_tier": "direct_connect",
        "log_posture": "direct",
        "db_host": "10.0.0.1",
        "db_port": 1521,
        "s3_bucket": None,
    }

    with patch("requests.get", return_value=mock_resp):
        adapter = _make_adapter()
        result = await adapter.resolve("acme")

    assert result["client_id"] == "acme"
    assert result["connectivity_tier"] == "direct_connect"
    assert result["log_posture"] == "direct"
    assert result["db_host"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_resolve_caches_result():
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "connectivity_tier": "s3_logs",
        "log_posture": "s3",
        "s3_bucket": "acme-logs",
    }

    with patch("requests.get", return_value=mock_resp) as mock_get:
        adapter = _make_adapter()
        r1 = await adapter.resolve("acme")
        r2 = await adapter.resolve("acme")

    assert mock_get.call_count == 1  # second call hit cache
    assert r1 == r2


@pytest.mark.asyncio
async def test_resolve_defaults_to_human_relay_on_error():
    from unittest.mock import patch

    with patch("requests.get", side_effect=ConnectionError("refused")):
        adapter = _make_adapter()
        result = await adapter.resolve("failing-client")

    assert result["connectivity_tier"] == "human_relay"
    assert result["log_posture"] == "human_relay"
    assert result["client_id"] == "failing-client"


@pytest.mark.asyncio
async def test_resolve_normalises_unknown_tier():
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "connectivity_tier": "unknown_tier",  # not a valid value
        "log_posture": "direct",
    }

    with patch("requests.get", return_value=mock_resp):
        adapter = _make_adapter()
        result = await adapter.resolve("weirdclient")

    assert result["connectivity_tier"] == "human_relay"


def test_invalidate_clears_cache():
    adapter = _make_adapter()
    adapter._cache["acme"] = ({"connectivity_tier": "direct_connect"}, 9999999999.0)
    adapter.invalidate("acme")
    assert "acme" not in adapter._cache
