"""HttpPhoenixAdapter — production Phoenix resolver via HTTP.

Phoenix is the internal web system that stores per-client connectivity details
(IP addresses, usernames, passwords, access tier). This adapter calls the
Phoenix HTTP API to resolve a client's connectivity profile.

Phoenix staleness is a load-bearing failure — wrong connectivity tier = wrong
tool routing for all subsequent tool calls. The adapter caches results in
memory (TTL configurable) and falls back to human_relay on any error.

The PoC fixture approach (register_poc_fixture in phoenix_resolver.py) remains
available for dev/test mode. This adapter targets production Phoenix pages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from support_orchestration.tools.mcp_server import PhoenixAdapter

logger = logging.getLogger(__name__)

_SAFE_DEFAULT: dict[str, Any] = {
    "connectivity_tier": "human_relay",
    "log_posture": "human_relay",
    "db_host": None,
    "s3_bucket": None,
}

_CACHE_TTL_SECONDS = 3600  # 1 hour


class HttpPhoenixAdapter(PhoenixAdapter):
    """
    Resolves per-client connectivity tier + log posture via Phoenix HTTP API.

    Expected JSON response from Phoenix:
        {
            "client_id": "acme",
            "connectivity_tier": "direct_connect" | "human_relay" | "s3_logs",
            "log_posture": "direct" | "s3" | "human_relay",
            "db_host": "192.168.1.10" | null,
            "db_port": 1521 | null,
            "s3_bucket": "acme-logs" | null
        }

    If the response does not match this schema, human_relay is assumed (safe default).

    Args:
        base_url:  Phoenix base URL (e.g. https://phoenix.internal).
        api_token: Service account bearer token for Phoenix API.
        timeout:   HTTP request timeout in seconds.
        ttl:       Cache TTL in seconds (default 1 hour).
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: float = 10.0,
        ttl: int = _CACHE_TTL_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout
        self._ttl = ttl
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}  # {client_id: (data, expires_at)}

    def _cache_get(self, client_id: str) -> dict[str, Any] | None:
        entry = self._cache.get(client_id)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        return None

    def _cache_set(self, client_id: str, data: dict[str, Any]) -> None:
        self._cache[client_id] = (data, time.monotonic() + self._ttl)

    def _fetch_sync(self, client_id: str) -> dict[str, Any]:
        import requests
        url = f"{self._base_url}/api/v1/clients/{client_id}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def _normalise(self, client_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        tier = raw.get("connectivity_tier", "human_relay")
        if tier not in ("direct_connect", "human_relay", "s3_logs"):
            logger.warning(
                "Phoenix returned unknown tier %r for %s; defaulting to human_relay",
                tier, client_id,
            )
            tier = "human_relay"

        posture = raw.get("log_posture", "human_relay")
        if posture not in ("direct", "s3", "human_relay"):
            posture = "human_relay"

        return {
            "client_id": client_id,
            "connectivity_tier": tier,
            "log_posture": posture,
            "db_host": raw.get("db_host"),
            "db_port": raw.get("db_port"),
            "s3_bucket": raw.get("s3_bucket"),
        }

    async def resolve(self, client_id: str) -> dict[str, Any]:
        cached = self._cache_get(client_id)
        if cached is not None:
            return cached

        try:
            raw = await asyncio.to_thread(self._fetch_sync, client_id)
            result = self._normalise(client_id, raw)
        except Exception as exc:
            logger.error(
                "PhoenixAdapter failed for client=%s (%s); defaulting to human_relay",
                client_id, exc,
            )
            result = {"client_id": client_id, **_SAFE_DEFAULT}

        self._cache_set(client_id, result)
        return result

    def invalidate(self, client_id: str) -> None:
        self._cache.pop(client_id, None)
