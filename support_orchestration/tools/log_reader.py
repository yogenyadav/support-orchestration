"""log_read — route log reads across the three client connectivity postures.

Three postures (per docs/2 §2.7):
  direct      — DB/logs readable after human opens the Direct Connect session
  s3          — Logs in AWS S3 {client-name} bucket via AWS MCP
  human_relay — No prod access; return relay sentinel → dialect layer asks engineer

The router checks the client's log_posture (resolved by phoenix_resolver) and
dispatches to the appropriate implementation. The human_relay path returns a
structured sentinel; the dialect layer (Prompt 7) converts that into a /ask message.
"""

from __future__ import annotations

from typing import Any

from .mcp_server import LogAdapter

RELAY_SENTINEL_KEY = "relay_required"


async def log_read(
    client_id: str,
    query: str,
    log_posture: str,
    log: LogAdapter,
    *,
    host: str | None = None,       # direct posture
    bucket: str | None = None,     # s3 posture
    prefix: str | None = None,     # s3 posture
) -> dict[str, Any]:
    """
    Read logs for a client, routing by connectivity posture.

    Returns:
        For direct/s3 postures: {"content": str, "posture": str}
        For human_relay:        {"relay_required": True, "question": str, "posture": "human_relay"}
    """
    if log_posture == "direct":
        if not host:
            raise ValueError("log_read: 'host' is required for direct posture")
        content = await log.read_direct(client_id=client_id, host=host, query=query)
        return {"content": content, "posture": "direct"}

    if log_posture == "s3":
        if not bucket:
            raise ValueError("log_read: 'bucket' is required for s3 posture")
        content = await log.read_s3(
            client_id=client_id, bucket=bucket, prefix=prefix or "", query=query,
        )
        return {"content": content, "posture": "s3"}

    if log_posture == "human_relay":
        return {
            RELAY_SENTINEL_KEY: True,
            "question": query,
            "posture": "human_relay",
            "client_id": client_id,
        }

    raise ValueError(f"log_read: unknown log_posture '{log_posture}'")


def is_relay_required(result: dict[str, Any]) -> bool:
    return bool(result.get(RELAY_SENTINEL_KEY))
