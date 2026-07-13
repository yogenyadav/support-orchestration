"""GithubApiAdapter — reads source code from GitHub via REST API.

Agents read both the base org (shared code: transitions, triggers, state values)
and the per-client org (overlay: state-name overrides, custom config) to discover
table/column names and state string values at runtime. Schema is never pre-loaded.

Uses the GitHub REST API v3 with a personal access token or GitHub App token.
`requests` is always available (part of standard support-orchestration dependencies).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from support_orchestration.tools.mcp_server import GithubAdapter

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_DEFAULT_TIMEOUT = 15.0


class GithubApiAdapter(GithubAdapter):
    """
    Read-only GitHub adapter using the REST API v3.

    Args:
        token:              GitHub personal access token or GitHub App installation token.
        client_org_prefix:  Prefix for client org names (default: "client-").
        timeout:            HTTP request timeout in seconds.
    """

    def __init__(
        self,
        token: str,
        client_org_prefix: str = "client-",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._client_org_prefix = client_org_prefix
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def read_file(self, org: str, repo: str, path: str, ref: str = "main") -> str:
        """
        Fetch file content from GitHub. Returns decoded text content.

        Raises:
            FileNotFoundError: if the file does not exist at the given ref.
            PermissionError:   if the token lacks access to the repo.
            RuntimeError:      on unexpected API errors.
        """
        import requests

        url = f"{_GITHUB_API}/repos/{org}/{repo}/contents/{path.lstrip('/')}"
        resp = requests.get(
            url,
            headers=self._headers(),
            params={"ref": ref},
            timeout=self._timeout,
        )

        if resp.status_code == 404:
            raise FileNotFoundError(
                f"GitHub: {org}/{repo}/{path}@{ref} not found"
            )
        if resp.status_code == 403:
            raise PermissionError(
                f"GitHub: access denied to {org}/{repo}/{path} — check token scopes"
            )
        if not resp.ok:
            raise RuntimeError(
                f"GitHub API error {resp.status_code} for {org}/{repo}/{path}: {resp.text[:200]}"
            )

        data: dict[str, Any] = resp.json()
        if data.get("type") != "file":
            raise ValueError(
                f"GitHub: {org}/{repo}/{path} is a {data.get('type')!r}, not a file"
            )

        encoding = data.get("encoding", "base64")
        content_raw = data.get("content", "")

        if encoding == "base64":
            return base64.b64decode(content_raw).decode("utf-8", errors="replace")
        return str(content_raw)


def build_from_env() -> GithubApiAdapter:
    """Build a GithubApiAdapter from environment variables."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN env var is required for GithubApiAdapter")
    prefix = os.environ.get("GITHUB_CLIENT_ORG_PREFIX", "client-")
    return GithubApiAdapter(token=token, client_org_prefix=prefix)
