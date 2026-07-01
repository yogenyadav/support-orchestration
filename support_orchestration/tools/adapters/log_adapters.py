"""SshLogAdapter — direct log access via SSH for Direct Connect clients.

Direct Connect clients have their logs readable via SSH to the per-client
access runner (a host with network reach to the client's infrastructure).
The human opens the session; the runner host+credentials come from Phoenix.

For S3-log clients, logs are read via the AWS MCP server (not this adapter).
For human_relay clients, log_read returns the relay sentinel; this adapter
is never called.

Install: pip install paramiko
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from support_orchestration.tools.mcp_server import LogAdapter

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_GREP_LINES = 500


class SshLogAdapter(LogAdapter):
    """
    Reads logs from client infrastructure via SSH to the per-client access runner.

    The runner is the gateway into the client network. We SSH to the runner
    and execute read-only commands (grep/tail) to pull log lines.

    Args:
        runner_host:     Hostname/IP of the per-client access runner.
        runner_user:     SSH username on the runner.
        runner_key_path: Path to SSH private key file (preferred over password).
        runner_password: SSH password (used if key_path is None).
        connect_timeout: SSH connection timeout in seconds.
    """

    def __init__(
        self,
        runner_host: str,
        runner_user: str,
        *,
        runner_key_path: str | None = None,
        runner_password: str | None = None,
        connect_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        try:
            import paramiko  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "paramiko is required for SSH log access. Install: pip install paramiko"
            ) from exc

        if runner_key_path is None and runner_password is None:
            raise ValueError("Either runner_key_path or runner_password must be provided")

        self._runner_host = runner_host
        self._runner_user = runner_user
        self._runner_key_path = runner_key_path
        self._runner_password = runner_password
        self._connect_timeout = connect_timeout

    def _make_client(self) -> Any:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
        client.connect(
            hostname=self._runner_host,
            username=self._runner_user,
            key_filename=self._runner_key_path,
            password=self._runner_password,
            timeout=self._connect_timeout,
            look_for_keys=self._runner_key_path is None,
        )
        return client

    def _run_command(self, client: Any, command: str) -> str:
        """Execute a read-only shell command and return stdout."""
        _, stdout, stderr = client.exec_command(command, timeout=self._connect_timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if err:
            logger.warning("SSH stderr for command %r: %s", command[:80], err[:200])
        return out

    @staticmethod
    def _build_grep_command(log_path_or_pattern: str, query: str, n_lines: int) -> str:
        """Build a safe read-only grep command. No shell injection: query is shell-quoted."""
        import shlex
        safe_query = shlex.quote(query)
        safe_path = shlex.quote(log_path_or_pattern)
        return f"grep -r -m {n_lines} {safe_query} {safe_path} 2>/dev/null || true"

    async def read_direct(
        self,
        client_id: str,
        host: str,
        query: str,
    ) -> str:
        """
        SSH to the runner and grep for `query` in the log path described by `host`.

        For Direct Connect clients, `host` is the log file path or directory pattern
        on the runner (e.g. "/var/log/wes/*.log" or "/srv/logs/wes-host-01/app.log").
        """
        log_path = host  # by convention, `host` is the log path for direct posture
        command = self._build_grep_command(log_path, query, _DEFAULT_GREP_LINES)

        def _sync() -> str:
            client = self._make_client()
            try:
                return self._run_command(client, command)
            finally:
                client.close()

        result = await asyncio.to_thread(_sync)
        logger.info(
            "SSH_LOG_READ client=%s path=%r query=%r bytes=%d",
            client_id, log_path[:60], query[:40], len(result),
        )
        return result or "(no matching log lines)"

    async def read_s3(
        self,
        client_id: str,
        bucket: str,
        prefix: str,
        query: str,
    ) -> str:
        raise NotImplementedError(
            "SshLogAdapter does not handle S3 log reads. "
            "S3-log clients are handled by the AWS MCP server."
        )
