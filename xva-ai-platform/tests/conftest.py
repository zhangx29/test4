import asyncio
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from sample_data.make_sample_data import build
from xva_mcp.server import build_server


STORE_FILES = {"sqlite": "xva_store.sqlite", "duckdb": "xva_store.duckdb"}


def backends() -> list:
    """SQLite always; DuckDB only when the package is installed (it is optional)."""
    from xva_mcp.db import duckdb_available

    marks = [] if duckdb_available() else [pytest.mark.skip(reason="duckdb package not installed")]
    return ["sqlite", pytest.param("duckdb", marks=marks)]


@pytest.fixture(scope="session")
def store_path(tmp_path_factory):
    # The tool tests run on SQLite, the default backend. test_store_backends.py checks DuckDB gives the same answers.
    path = tmp_path_factory.mktemp("store") / STORE_FILES["sqlite"]
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
    monkeypatch.delenv("XVA_STORE_BACKEND", raising=False)
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
