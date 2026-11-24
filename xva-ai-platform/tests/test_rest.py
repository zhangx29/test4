"""REST API: same tools, entitlements and audit as MCP; valid OpenAPI 3.0 for API plugins and connectors."""

import json
import os

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from xva_mcp import rest, tools  # noqa: E402

D1, D2 = "2026-11-19", "2026-11-20"


@pytest.fixture
def client():
    return TestClient(rest.create_app())


def test_tool_call_returns_result_with_provenance(client):
    r = client.get("/api/v1/get_xva_summary", params={"as_of": D1, "level": "book"})
    assert r.status_code == 200
    body = r.json()
    assert {row["key"] for row in body["rows"]} == {"XVA-CORP", "XVA-FIN"}
    assert body["provenance"]["run_ids"] == ["XVA-20261119-01"]
    direct = tools.call("get_xva_summary", {"as_of": D1, "level": "book"})
    assert body["totals"]["total"] == pytest.approx(direct.totals.total)


def test_errors_map_to_status_codes(client, monkeypatch):
    r = client.get("/api/v1/get_xva_movers", params={"start": D1, "end": D2, "top_n": 0})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"
    r = client.get("/api/v1/get_xva_summary", params={"as_of": "2026-12-25"})
    assert r.status_code == 404 and "Latest available date" in r.json()["message"]
    r = client.get("/api/v1/get_xva_summary", params={"unexpected": "1"})
    assert r.status_code == 422
    assert client.get("/api/v1/drop_table").status_code == 404
    assert client.post("/api/v1/get_xva_summary").status_code == 405  # read-only: GET only
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    r = client.get("/api/v1/get_xva_movers", params={"start": D1, "end": D2, "level": "counterparty"})
    assert r.status_code == 403 and r.json()["error"] == "access_denied"


def test_audit_says_via_rest(client):
    client.get("/api/v1/get_data_status", params={"as_of": D2})
    last = [json.loads(x) for x in open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8")][-1]
    assert last["via"] == "rest" and last["tool"] == "get_data_status" and last["outcome"] == "ok"


# ---------------------------------------------------------------- sign-in


class FakeVerifier:
    """Stands in for JwtTokenVerifier: 'good' is a valid user token, 'noscope' lacks xva.read."""

    async def verify_token(self, token):
        from mcp.server.auth.provider import AccessToken

        if token not in ("good", "noscope", "apponly"):
            return None
        claims = {"scp": "xva.read", "oid": "user-1", "groups": ["xva-exec"]}
        if token == "apponly":
            claims = {"roles": ["xva-desk"], "oid": "app-1"}  # no scp: an app acting as itself
        return AccessToken(
            token=token,
            client_id="client-1",
            scopes=[] if token == "noscope" else ["xva.read"],
            subject=claims["oid"],
            claims=claims,
        )


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.delenv("XVA_ENV")  # production: the dev identity must not be used
    return TestClient(rest.create_app(verifier=FakeVerifier(), base_url="https://xva-api.test"))


def test_sign_in_is_required(secured):
    r = secured.get("/api/v1/get_data_status")
    assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"]
    r = secured.get("/api/v1/get_data_status", headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_token"
    r = secured.get("/api/v1/get_data_status", headers={"Authorization": "Bearer noscope"})
    assert r.status_code == 403 and r.json()["error"] == "insufficient_scope"
    r = secured.get("/api/v1/get_data_status", headers={"Authorization": "Bearer apponly"})
    assert r.status_code == 403 and "signed-in user" in r.json()["message"]


def test_token_role_is_enforced(secured):
    ok = {"Authorization": "Bearer good"}  # the token's group maps to xva-exec
    assert secured.get("/api/v1/get_xva_summary", params={"as_of": D1}, headers=ok).status_code == 200
    r = secured.get("/api/v1/search_counterparties", params={"query": "Maple"}, headers=ok)
    assert r.status_code == 403
    last = [json.loads(x) for x in open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8")][-1]
    assert last["user"] == "user-1" and last["role"] == "xva-exec" and last["via"] == "rest"


# ---------------------------------------------------------------- OpenAPI


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


@pytest.mark.parametrize("auth", [False, True])
def test_openapi_is_3_0_and_complete(auth):
    spec = rest.openapi(base_url="https://xva-api.test", auth=auth)
    assert spec["openapi"] == "3.0.3"
    ops = {p["get"]["operationId"]: p["get"] for p in spec["paths"].values()}
    assert set(ops) == set(tools.TOOL_BY_NAME)
    for name, op in ops.items():
        assert op["description"] and op["summary"]
        for param in op["parameters"]:
            assert param.get("description"), f"{name}.{param['name']} has no description"
    schemas = spec["components"]["schemas"]
    for ref in _refs(spec):
        assert ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[1] in schemas, ref
    text = json.dumps(spec)
    assert '"type": "null"' not in text and '"$defs"' not in text and '"const"' not in text
    assert ("security" in spec) is auth


def test_openapi_passes_a_validator_when_available():
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(rest.openapi(base_url="https://xva-api.test", auth=False))
    validator.validate(
        rest.openapi(
            base_url="https://xva-api.test",
            auth=True,
            oauth={"authorize_url": "https://login.test/a", "token_url": "https://login.test/t", "scope": "api://x/xva.read"},
        )
    )


def test_openapi_endpoint_and_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/openapi.json").json()["paths"]
