"""phoenix_resolve — resolve per-client connectivity tier and log posture.

Phoenix is a set of internal web pages (one per client) that list the IP addresses,
usernames, and passwords to connect to each client's infrastructure.

For PoC: user provides connection details as a fixture YAML/dict — no web scraping.
For production: implement a web scraper or REST adapter against the Phoenix pages.

Results are cached per client. Phoenix staleness is a load-bearing failure —
wrong connectivity tier = wrong tool routing for all subsequent tool calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_cache: dict[str, dict[str, Any]] = {}

# ── PoC fixture store ─────────────────────────────────────────────────────────
# Load from clients/<client_id>/config.yaml connectivity block, or from a
# manually provided dict via register_poc_fixture().

_poc_fixtures: dict[str, dict[str, Any]] = {}

POC_CLIENT_CONFIG_DIR = Path(__file__).parents[2] / "clients"


def register_poc_fixture(client_id: str, fixture: dict[str, Any]) -> None:
    """
    Register PoC connection details for a client.

    Call this in tests or dev setup instead of scraping Phoenix pages.

    Example fixture:
        {
            "connectivity_tier": "direct_connect",
            "log_posture": "direct",
            "db_host": "192.168.1.10",
            "db_port": 1521,
            "db_user": "readonly_user",
            "db_password": "...",       # from secrets vault in prod
            "s3_bucket": None,
        }
    """
    _poc_fixtures[client_id] = fixture
    _cache.pop(client_id, None)   # invalidate any cached value


def _load_from_client_config(client_id: str) -> dict[str, Any] | None:
    """Try to load connectivity details from clients/<client_id>/config.yaml."""
    config_path = POC_CLIENT_CONFIG_DIR / client_id / "config.yaml"
    if not config_path.exists():
        return None
    with config_path.open() as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}
    conn = cfg.get("connectivity", {})
    if not conn:
        return None
    return {
        "connectivity_tier": conn.get("tier", "human_relay"),
        "log_posture": conn.get("log_posture", "human_relay"),
        "db_host": conn.get("db_host"),
        "s3_bucket": conn.get("s3_bucket"),
    }


async def phoenix_resolve(
    client_id: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Resolve the access tier and log posture for a client.

    Resolution order:
      1. Cache (unless force_refresh)
      2. PoC fixture registered via register_poc_fixture()
      3. clients/<client_id>/config.yaml connectivity block
      4. Fallback: human_relay (safest default — never assumes access)

    Returns:
        {
            "client_id": str,
            "connectivity_tier": "direct_connect" | "human_relay" | "s3_logs",
            "log_posture": "direct" | "s3" | "human_relay",
            "db_host": str | None,
            "s3_bucket": str | None,
        }
    """
    if not force_refresh and client_id in _cache:
        return _cache[client_id]

    result: dict[str, Any] | None = None

    # PoC fixture takes priority over config file
    if client_id in _poc_fixtures:
        result = dict(_poc_fixtures[client_id])
    else:
        result = _load_from_client_config(client_id)

    if result is None:
        # Safe fallback — never assume direct access we don't know about
        result = {
            "connectivity_tier": "human_relay",
            "log_posture": "human_relay",
            "db_host": None,
            "s3_bucket": None,
        }

    result["client_id"] = client_id
    _cache[client_id] = result
    return result


def invalidate_cache(client_id: str | None = None) -> None:
    if client_id:
        _cache.pop(client_id, None)
    else:
        _cache.clear()
