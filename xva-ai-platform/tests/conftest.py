import asyncio
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from sample_data.make_sample_data import build
from xva_mcp.server import build_server


@pytest.fixture(scope="session")
def store_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("store") / "xva_store.duckdb"
    build(path)
    return path


@pytest.fixture(autouse=True)
def dev_env(store_path, tmp_path, monkeypatch):
    monkeypatch.setenv("XVA_STORE_PATH", str(store_path))
    monkeypatch.setenv("XVA_ENV", "dev")
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-desk")
    monkeypatch.setenv("XVA_DEV_USER", "test-user")
    monkeypatch.setenv("XVA_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("XVA_ROLE_MAP", raising=False)
    yield


def _run(coro_fn):
    return asyncio.run(coro_fn())


@pytest.fixture
def call():
    """Call a tool through a real MCP client session against the in-process server."""

    def _call(name: str, args: dict[str, Any] | None = None):
        async def go():
            async with create_connected_server_and_client_session(build_server(auth=False)) as client:
                return await client.call_tool(name, args or {})

        return _run(go)

    return _call


@pytest.fixture
def list_tools():
    def _list():
        async def go():
            async with create_connected_server_and_client_session(build_server(auth=False)) as client:
                return (await client.list_tools()).tools

        return _run(go)

    return _list
