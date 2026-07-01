import pytest

from support_orchestration.tools.github_reader import github_read, make_base_read
from support_orchestration.tools.mcp_server import StubGithubAdapter


@pytest.mark.asyncio
async def test_github_read_stub_returns_content():
    adapter = StubGithubAdapter()
    result = await github_read("client-1", "src/wes/order_states.py", github=adapter)
    assert "content" in result
    assert "stub content" in result["content"]
    assert result["path"] == "src/wes/order_states.py"
    assert result["ref"] == "main"


@pytest.mark.asyncio
async def test_github_read_infers_client_org(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ORG_PREFIX", "client-")
    adapter = StubGithubAdapter()
    result = await github_read("test-client", "src/transitions.py", github=adapter)
    assert result["org"] == "client-test-client"


@pytest.mark.asyncio
async def test_github_read_explicit_org_override():
    adapter = StubGithubAdapter()
    result = await github_read(
        "test-client", "src/wes.py", org="base-corp", github=adapter
    )
    assert result["org"] == "base-corp"


@pytest.mark.asyncio
async def test_github_read_default_repo_is_core():
    adapter = StubGithubAdapter()
    result = await github_read("client-1", "src/states.py", github=adapter)
    assert result["repo"] == "core"


@pytest.mark.asyncio
async def test_github_read_explicit_repo():
    adapter = StubGithubAdapter()
    result = await github_read("client-1", "config/wcs.yaml", repo="wcs-service", github=adapter)
    assert result["repo"] == "wcs-service"


def test_make_base_read_uses_env(monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_ORG", "my-company")
    kwargs = make_base_read("src/order_states.py")
    assert kwargs["org"] == "my-company"
    assert kwargs["repo"] == "core"
    assert kwargs["ref"] == "main"
