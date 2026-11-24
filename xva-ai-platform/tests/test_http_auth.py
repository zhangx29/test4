import pytest

pytest.importorskip("mcp", reason="mcp package not installed")
pytest.importorskip("jwt", reason="jwt package not installed")

import warnings

import jwt
from starlette.testclient import TestClient

from xva_mcp.auth import JwtTokenVerifier
from xva_mcp.server import build_server

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
HEADERS = {"Accept": "application/json, text/event-stream"}


def test_http_server_requires_a_valid_token(monkeypatch):
    monkeypatch.setenv("XVA_OAUTH_ISSUER", "https://login.example.com/tenant/v2.0")
    monkeypatch.setenv("XVA_OAUTH_AUDIENCE", "api://xva-mcp, api://auth-xva-sso.example/xva")  # VS Code + M365
    monkeypatch.setenv("XVA_ALLOWED_CLIENT_IDS", "vscode-client, ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b")
    monkeypatch.setenv("XVA_OAUTH_JWKS_URL", "https://login.example.com/keys")
    monkeypatch.setenv("XVA_RESOURCE_URL", "https://xva-mcp.apps.internal.example/mcp")

    def no_network(self, token):  # keep the test offline: JWKS lookup fails as for an unknown key
        raise jwt.PyJWKClientError("no matching key")

    monkeypatch.setattr(JwtTokenVerifier, "_signing_key", no_network)
    app = build_server(auth=True).streamable_http_app()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(app) as client:
            missing = client.post("/mcp", json=INIT, headers=HEADERS)
            assert missing.status_code == 401
            assert "resource_metadata=" in missing.headers["www-authenticate"]

            forged = client.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer not-a-jwt"})
            assert forged.status_code == 401

            meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
            assert meta["resource"] == "https://xva-mcp.apps.internal.example/mcp"
            assert meta["authorization_servers"] == ["https://login.example.com/tenant/v2.0"]
            assert meta["scopes_supported"] == ["xva.read"]
