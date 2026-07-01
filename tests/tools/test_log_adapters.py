"""Tests for SshLogAdapter — SSH-based direct log access."""

from __future__ import annotations

import pytest


def test_ssh_import_error_on_missing_paramiko(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "paramiko":
            raise ImportError("No module named 'paramiko'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="paramiko"):
        from support_orchestration.tools.adapters.log_adapters import SshLogAdapter
        SshLogAdapter("runner", "user", runner_key_path="/key")


def test_ssh_requires_credential():
    """Must supply either key_path or password."""
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"paramiko": mock.MagicMock()}):
        from importlib import reload
        import support_orchestration.tools.adapters.log_adapters as mod
        reload(mod)
        with pytest.raises(ValueError, match="key_path or runner_password"):
            mod.SshLogAdapter("runner", "user")


def test_ssh_grep_command_safe():
    """Shell injection: query is shell-quoted in the grep command."""
    from support_orchestration.tools.adapters.log_adapters import SshLogAdapter
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"paramiko": mock.MagicMock()}):
        from importlib import reload
        import support_orchestration.tools.adapters.log_adapters as mod
        reload(mod)
        # Malicious query containing shell metacharacters
        cmd = mod.SshLogAdapter._build_grep_command(
            "/var/log/wes/*.log",
            "search term; rm -rf /",
            500,
        )
    # The dangerous characters must be shell-quoted
    assert "rm -rf /" not in cmd or "'" in cmd


@pytest.mark.asyncio
async def test_read_direct_returns_grep_output():
    """read_direct SSHs to runner and returns grep output."""
    from importlib import reload
    from unittest.mock import MagicMock, patch

    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"2024-01-01 10:00 ERROR consumer died\n"
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

    mock_paramiko = MagicMock()
    mock_paramiko.SSHClient.return_value = mock_client
    mock_paramiko.AutoAddPolicy = MagicMock()

    with patch.dict("sys.modules", {"paramiko": mock_paramiko}):
        import support_orchestration.tools.adapters.log_adapters as mod
        reload(mod)
        adapter = mod.SshLogAdapter("runner-host", "runner-user", runner_key_path="/key")
        result = await adapter.read_direct("acme", "/var/log/wes/*.log", "consumer")

    assert "ERROR" in result or "consumer" in result


@pytest.mark.asyncio
async def test_read_s3_raises_not_implemented():
    """SshLogAdapter.read_s3 must raise NotImplementedError — handled by AWS MCP."""
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"paramiko": mock.MagicMock()}):
        from importlib import reload
        import support_orchestration.tools.adapters.log_adapters as mod
        reload(mod)
        adapter = mod.SshLogAdapter("runner", "user", runner_password="pw")

    with pytest.raises(NotImplementedError, match="AWS MCP"):
        await adapter.read_s3("acme", "acme-logs", "wes/", "error")
