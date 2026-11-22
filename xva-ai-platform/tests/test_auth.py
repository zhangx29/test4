import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from xva_mcp.auth import M365_COPILOT_TOKEN_STORE_CLIENT_ID, JwtTokenVerifier, split_list

TENANT = "11111111-2222-3333-4444-555555555555"
ISSUER_V2 = f"https://login.microsoftonline.com/{TENANT}/v2.0"
ISSUER_V1 = f"https://sts.windows.net/{TENANT}/"
API_AUDIENCE = "6f1c1d2e-0000-4000-8000-00000000a001"  # v2 tokens: the API's client ID
M365_SSO_AUDIENCE = "api://auth-xva-sso.example/6f1c1d2e"  # placeholder for the SSO Application ID URI
VSCODE_CLIENT = "vscode-client-id-from-a-real-token"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


def make_verifier(keypair, monkeypatch, **kwargs):
    args = {"issuers": ISSUER_V2, "audiences": API_AUDIENCE, "jwks_url": "https://login.example.com/keys", **kwargs}
    v = JwtTokenVerifier(**args)
    monkeypatch.setattr(v, "_signing_key", lambda token: keypair[1])
    return v


def token(keypair, **overrides):
    claims = {
        "iss": ISSUER_V2,
        "aud": API_AUDIENCE,
        "exp": int(time.time()) + 600,
        "oid": "user-123",
        "azp": VSCODE_CLIENT,
        "scp": "xva.read",
        "roles": ["xva-desk"],
        **overrides,
    }
    return jwt.encode(claims, keypair[0], algorithm="RS256")


def verify(verifier, tok):
    return asyncio.run(verifier.verify_token(tok))


def test_valid_token_yields_subject_client_and_roles(keypair, monkeypatch):
    access = verify(make_verifier(keypair, monkeypatch), token(keypair))
    assert access is not None
    assert access.subject == "user-123"
    assert access.client_id == VSCODE_CLIENT
    assert access.scopes == ["xva.read"]
    assert access.resource == API_AUDIENCE
    assert access.claims["roles"] == ["xva-desk"]


@pytest.mark.parametrize(
    "override",
    [{"aud": "api://something-else"}, {"iss": "https://evil.example.com"}, {"exp": int(time.time()) - 10}],
)
def test_wrong_audience_issuer_or_expired_is_rejected(keypair, monkeypatch, override):
    assert verify(make_verifier(keypair, monkeypatch), token(keypair, **override)) is None


def test_one_server_accepts_vscode_and_m365_audiences(keypair, monkeypatch):
    v = make_verifier(keypair, monkeypatch, audiences=f"{API_AUDIENCE}, {M365_SSO_AUDIENCE}")
    vscode = verify(v, token(keypair))
    m365 = verify(v, token(keypair, aud=M365_SSO_AUDIENCE, azp=M365_COPILOT_TOKEN_STORE_CLIENT_ID))
    assert vscode.resource == API_AUDIENCE
    assert m365.resource == M365_SSO_AUDIENCE and m365.client_id == M365_COPILOT_TOKEN_STORE_CLIENT_ID
    listed = verify(v, token(keypair, aud=["https://other.example", M365_SSO_AUDIENCE]))
    assert listed.resource == M365_SSO_AUDIENCE


def test_client_allowlist(keypair, monkeypatch):
    v = make_verifier(
        keypair, monkeypatch, allowed_client_ids=[VSCODE_CLIENT, M365_COPILOT_TOKEN_STORE_CLIENT_ID]
    )
    assert verify(v, token(keypair)) is not None
    assert verify(v, token(keypair, azp=M365_COPILOT_TOKEN_STORE_CLIENT_ID)) is not None
    assert verify(v, token(keypair, azp="some-other-app")) is None
    # v1 tokens name the client in appid instead of azp
    assert verify(v, token(keypair, azp=None, appid=VSCODE_CLIENT)) is not None


def test_v1_issuer_only_when_listed(keypair, monkeypatch):
    v2_only = make_verifier(keypair, monkeypatch)
    both = make_verifier(keypair, monkeypatch, issuers=[ISSUER_V2, ISSUER_V1])
    v1_token = token(keypair, iss=ISSUER_V1)
    assert verify(v2_only, v1_token) is None
    assert verify(both, v1_token) is not None


def test_lists_from_environment_strings():
    assert split_list(" a, b ,,c ") == ["a", "b", "c"]
    assert split_list(None) == []
    with pytest.raises(ValueError):
        JwtTokenVerifier(issuers="", audiences="x", jwks_url="https://login.example.com/keys")
