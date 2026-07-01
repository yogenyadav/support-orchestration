"""Tests for GithubApiAdapter — GitHub REST API v3 file reader."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from support_orchestration.tools.adapters.github_adapter import GithubApiAdapter, build_from_env
from support_orchestration.tools.mcp_server import GithubAdapter


def _make_adapter() -> GithubApiAdapter:
    return GithubApiAdapter(token="ghp_test123")


def _mock_response(content: str, status_code: int = 200, encoding: str = "base64") -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.ok = (status_code == 200)
    encoded = base64.b64encode(content.encode()).decode()
    mock_resp.json.return_value = {
        "type": "file",
        "encoding": encoding,
        "content": encoded + "\n",   # GitHub adds newline to base64
    }
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = ""
    return mock_resp


# ── ABC compliance ────────────────────────────────────────────────────────────

def test_github_adapter_is_github_adapter():
    assert isinstance(_make_adapter(), GithubAdapter)


# ── read_file ─────────────────────────────────────────────────────────────────

def test_read_file_success():
    adapter = _make_adapter()
    content = "STATE_PRIORITIZED = 'prioritized'\n"
    with patch("requests.get", return_value=_mock_response(content)) as mock_get:
        result = adapter.read_file("client-acme", "core", "src/wes/states.py", "main")

    assert result == content
    call_url = mock_get.call_args[0][0]
    assert "client-acme" in call_url
    assert "core" in call_url
    assert "states.py" in call_url


def test_read_file_not_found_raises():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.ok = False

    with patch("requests.get", return_value=mock_resp):
        adapter = _make_adapter()
        with pytest.raises(FileNotFoundError, match="not found"):
            adapter.read_file("org", "repo", "missing.py")


def test_read_file_permission_denied_raises():
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.ok = False

    with patch("requests.get", return_value=mock_resp):
        adapter = _make_adapter()
        with pytest.raises(PermissionError, match="access denied"):
            adapter.read_file("private-org", "repo", "secret.py")


def test_read_file_sends_auth_header():
    adapter = GithubApiAdapter(token="ghp_mytoken")
    with patch("requests.get", return_value=_mock_response("content")) as mock_get:
        adapter.read_file("org", "repo", "file.py")

    headers = mock_get.call_args[1]["headers"]
    assert "ghp_mytoken" in headers.get("Authorization", "")


# ── build_from_env ────────────────────────────────────────────────────────────

def test_build_from_env_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        build_from_env()


def test_build_from_env_succeeds(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_CLIENT_ORG_PREFIX", "wh-client-")
    adapter = build_from_env()
    assert adapter._token == "ghp_test"
    assert adapter._client_org_prefix == "wh-client-"
