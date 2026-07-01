"""Tests for phoenix_resolve — PoC fixture loading and fallback behaviour."""

import pytest

from support_orchestration.tools.phoenix_resolver import (
    invalidate_cache,
    phoenix_resolve,
    register_poc_fixture,
)


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    invalidate_cache()


@pytest.mark.asyncio
async def test_returns_human_relay_when_no_fixture() -> None:
    result = await phoenix_resolve("unknown-client")
    assert result["connectivity_tier"] == "human_relay"
    assert result["log_posture"] == "human_relay"
    assert result["client_id"] == "unknown-client"


@pytest.mark.asyncio
async def test_poc_fixture_overrides_default() -> None:
    register_poc_fixture("acme", {
        "connectivity_tier": "direct_connect",
        "log_posture": "direct",
        "db_host": "10.0.0.1",
        "s3_bucket": None,
    })
    result = await phoenix_resolve("acme")
    assert result["connectivity_tier"] == "direct_connect"
    assert result["db_host"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_result_is_cached() -> None:
    register_poc_fixture("acme", {"connectivity_tier": "s3_logs", "log_posture": "s3",
                                   "db_host": None, "s3_bucket": "acme-logs"})
    r1 = await phoenix_resolve("acme")
    r2 = await phoenix_resolve("acme")
    assert r1 is r2   # same object = cached


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache() -> None:
    register_poc_fixture("acme", {"connectivity_tier": "human_relay", "log_posture": "human_relay",
                                   "db_host": None, "s3_bucket": None})
    r1 = await phoenix_resolve("acme")
    r2 = await phoenix_resolve("acme", force_refresh=True)
    assert r1["client_id"] == r2["client_id"]


@pytest.mark.asyncio
async def test_loads_from_client_config_yaml() -> None:
    # example-client has config.yaml with connectivity.tier = human_relay
    result = await phoenix_resolve("example-client")
    assert result["connectivity_tier"] == "human_relay"
