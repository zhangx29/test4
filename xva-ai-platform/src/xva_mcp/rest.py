"""REST API over the same eight tools, with an OpenAPI 3.0 description. For front ends that can't use MCP:
Microsoft 365 Copilot API plugins, Copilot Studio, the bank's own AI platform, or plain scripts.

    GET /api/v1/<tool>?<arguments>   e.g. /api/v1/get_xva_summary?as_of=2026-11-19&level=book
    GET /openapi.json                the OpenAPI description (operationId = tool name)
    GET /healthz

Development (sample data, no sign-in, 127.0.0.1 only):
    XVA_ENV=dev XVA_STORE_PATH=sample_data/xva_store.duckdb python -m xva_mcp.rest --port 8001

Production (behind the bank's TLS ingress; the same token settings as the MCP server):
    XVA_OAUTH_ISSUER=... XVA_OAUTH_AUDIENCE=... XVA_OAUTH_JWKS_URL=... XVA_API_BASE_URL=https://xva-api... \\
    python -m xva_mcp.rest --host 0.0.0.0 --port 8001

Export the description for an API plugin or connector without starting the server:
    python -m xva_mcp.rest --export-openapi openapi.json

Entitlements and the audit log are the ones the MCP server uses (tools.py); audit lines say via=rest.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__, audit, entitlements, tools

_log = logging.getLogger("xva_mcp.rest")
API_PREFIX = tools.API_PREFIX


# ---------------------------------------------------------------- request handling


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


def _invoke(token: Any, name: str, kwargs: dict[str, Any]) -> Any:
    """Run one tool as the token's user. Runs in a worker thread, so the context is set here."""
    t1 = entitlements.request_token.set(token)
    t2 = audit.channel.set("rest")
    try:
        return tools.TOOL_BY_NAME[name](**kwargs)
    finally:
        audit.channel.reset(t2)
        entitlements.request_token.reset(t1)


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(x) for x in err.get("loc", ())) or "request"
        parts.append(f"{where}: {err.get('msg')}")
    return "; ".join(parts)


def create_app(
    *,
    verifier: Any | None = None,
    scopes: list[str] | None = None,
    base_url: str = "http://127.0.0.1:8001",
    oauth: dict[str, str] | None = None,
) -> Starlette:
    """verifier=None means development: no sign-in, identity from XVA_DEV_* (requires XVA_ENV=dev)."""
    needed_scopes = list(scopes or ["xva.read"])
    spec = openapi(base_url=base_url, auth=verifier is not None, oauth=oauth)

    async def call_tool(request: Request) -> JSONResponse:
        name = request.path_params["tool"]
        if name not in tools.TOOL_BY_NAME:
            return _error(404, "unknown_tool", f"No tool named {name}. See /openapi.json.")

        token = None
        if verifier is not None:
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not value.strip():
                return _error(401, "unauthorized", "Sign-in required.", {"WWW-Authenticate": 'Bearer realm="xva-data"'})
            token = await verifier.verify_token(value.strip())
            if token is None:
                return _error(
                    401, "invalid_token", "The access token is invalid or expired.",
                    {"WWW-Authenticate": 'Bearer realm="xva-data", error="invalid_token"'},
                )
            missing = [s for s in needed_scopes if s not in (token.scopes or [])]
            if missing:
                return _error(
                    403, "insufficient_scope", f"The token lacks scope {' '.join(missing)}.",
                    {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{" ".join(needed_scopes)}"'},
                )

        try:
            kwargs = tools.validate_args(name, dict(request.query_params))
        except ValidationError as exc:
            return _error(422, "invalid_request", _validation_message(exc))

        try:
            result = await run_in_threadpool(_invoke, token, name, kwargs)
        except tools.ToolDenied as exc:
            return _error(403, "access_denied", str(exc))
        except tools.DataMissing as exc:
            return _error(404, "not_found", str(exc))
        except Exception:  # never leak internals to the caller; the details go to the server log
            _log.exception("Tool %s failed", name)
            return _error(500, "internal_error", "The XVA data service hit an unexpected error. It has been logged.")
        return JSONResponse(result.model_dump(mode="json"))

    async def openapi_json(request: Request) -> JSONResponse:
        return JSONResponse(spec)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route(API_PREFIX + "/{tool}", call_tool, methods=["GET"]),
            Route("/openapi.json", openapi_json, methods=["GET"]),
            Route("/healthz", health, methods=["GET"]),
        ]
    )


# ---------------------------------------------------------------- OpenAPI 3.0 description

_NULL = {"type": "null"}


def _oas30(node: Any) -> Any:
    """Turn pydantic's JSON Schema (OpenAPI 3.1 style) into OpenAPI 3.0.3, which more tools accept:
    'X or null' becomes nullable, const becomes a one-value enum."""
    if isinstance(node, list):
        return [_oas30(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: _oas30(v) for k, v in node.items()}
    for key in ("anyOf", "oneOf"):
        options = node.get(key)
        if isinstance(options, list) and _NULL in options:
            rest = [o for o in options if o != _NULL]
            del node[key]
            if len(rest) == 1:
                inner = rest[0]
                node = {**node, "allOf": [inner]} if "$ref" in inner else {**inner, **node}
            else:
                node[key] = rest
            node["nullable"] = True
    if "const" in node:
        node["enum"] = [node.pop("const")]
    if isinstance(node.get("examples"), list) and node["examples"]:
        node["example"] = node.pop("examples")[0]
    node.pop("$schema", None)
    return node


def _summary(fn: Any) -> str:
    text = tools.description(fn)
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= 120 else first[:117] + "..."


def openapi(*, base_url: str, auth: bool, oauth: dict[str, str] | None = None) -> dict[str, Any]:
    from pydantic.json_schema import models_json_schema

    results = sorted({tools.result_model(n) for n in tools.TOOL_BY_NAME}, key=lambda m: m.__name__)
    _, top = models_json_schema(
        [(m, "serialization") for m in results], ref_template="#/components/schemas/{model}"
    )
    schemas: dict[str, Any] = {k: _oas30(v) for k, v in top.get("$defs", {}).items()}
    schemas["Error"] = {
        "type": "object",
        "required": ["error", "message"],
        "properties": {
            "error": {"type": "string", "description": "Machine-readable code, e.g. access_denied"},
            "message": {"type": "string", "description": "Explanation to show the user"},
        },
    }

    def err(text: str) -> dict[str, Any]:
        return {"description": text, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}

    paths: dict[str, Any] = {}
    for name, fn in tools.TOOL_BY_NAME.items():
        req = tools.request_model(name).model_json_schema()
        required = set(req.get("required", []))
        params = []
        for pname, raw in req.get("properties", {}).items():
            schema = _oas30(raw)
            desc = schema.pop("description", None)
            schema.pop("title", None)
            schema.pop("nullable", None)  # an omitted query parameter already means "none"
            if schema.get("default", 0) is None:
                schema.pop("default")
            param: dict[str, Any] = {"name": pname, "in": "query", "required": pname in required, "schema": schema}
            if desc:
                param["description"] = desc
            params.append(param)
        responses = {
            "200": {
                "description": "Result with provenance (source, as-of date, run IDs, unit, sign convention)",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{tools.result_model(name).__name__}"}}
                },
            },
            "403": err("The caller's role may not see this (e.g. counterparty detail for executives)"),
            "404": err("No data for this date or selection"),
            "422": err("Invalid arguments"),
        }
        if auth:
            responses["401"] = err("Sign-in required, or the token is invalid or expired")
        paths[f"{API_PREFIX}/{name}"] = {
            "get": {
                "operationId": name,
                "summary": _summary(fn),
                "description": tools.description(fn),
                "parameters": params,
                "responses": responses,
            }
        }

    doc: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "XVA data API",
            "version": __version__,
            "description": tools.INSTRUCTIONS + " Read-only. Figures are official end-of-day values from the curated store.",
        },
        "servers": [{"url": base_url}],
        "paths": paths,
        "components": {"schemas": schemas},
    }
    if auth:
        oauth = oauth or {}
        if oauth.get("authorize_url") and oauth.get("token_url"):
            scope = oauth.get("scope", "xva.read")
            doc["components"]["securitySchemes"] = {
                "entra": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": oauth["authorize_url"],
                            "tokenUrl": oauth["token_url"],
                            "scopes": {scope: "Read official XVA figures"},
                        }
                    },
                }
            }
            doc["security"] = [{"entra": [scope]}]
        else:
            doc["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
            doc["security"] = [{"bearer": []}]
    return doc


def _oauth_from_env() -> dict[str, str]:
    env = os.environ
    return {
        "authorize_url": env.get("XVA_OAUTH_AUTHORIZE_URL", ""),
        "token_url": env.get("XVA_OAUTH_TOKEN_URL", ""),
        "scope": env.get("XVA_OAUTH_SCOPE", "xva.read"),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="XVA data REST API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--export-openapi", metavar="PATH", help="write the OpenAPI description to PATH and exit")
    a = p.parse_args()

    dev = os.environ.get("XVA_ENV") == "dev"
    base_url = os.environ.get("XVA_API_BASE_URL", f"http://{a.host}:{a.port}")
    if a.export_openapi:
        doc = openapi(base_url=base_url, auth=not dev, oauth=_oauth_from_env())
        with open(a.export_openapi, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        print(f"Wrote {a.export_openapi} ({len(doc['paths'])} operations, servers: {base_url})")
        return

    if dev:
        if a.host != "127.0.0.1":
            raise SystemExit("Without sign-in (XVA_ENV=dev) the API may only listen on 127.0.0.1.")
        app = create_app(base_url=base_url)
    else:
        from .auth import required_scopes, verifier_from_env

        app = create_app(
            verifier=verifier_from_env(), scopes=required_scopes(), base_url=base_url, oauth=_oauth_from_env()
        )

    import uvicorn

    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
