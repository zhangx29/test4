"""XVA data MCP server: read-only, typed tools with entitlement checks and an audit line per call.

Run locally for development (sample data, stdio):
    XVA_ENV=dev XVA_STORE_PATH=sample_data/xva_store.sqlite python -m xva_mcp.server

The store is SQLite by default (no extra package) or DuckDB; see db.py for how the backend is chosen.

Run in production (Streamable HTTP behind TLS, tokens from the bank's identity provider):
    XVA_OAUTH_ISSUER=... XVA_OAUTH_AUDIENCE=... XVA_OAUTH_JWKS_URL=... XVA_RESOURCE_URL=... \
    python -m xva_mcp.server --transport streamable-http --host 0.0.0.0 --port 8080

XVA_OAUTH_AUDIENCE and XVA_ALLOWED_CLIENT_IDS take comma-separated lists, so one server can serve
VS Code / GitHub Copilot, Microsoft 365 Copilot and Copilot Studio at the same time (see auth.py).

The tools themselves live in tools.py, shared with the REST API (rest.py) and the command line (cli.py).
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .tools import (  # noqa: F401  (re-exported: the tools were defined here before tools.py existed)
    INSTRUCTIONS,
    TOOLS,
    get_counterparty_xva_timeseries,
    get_data_status,
    get_definition,
    get_pnl_attribution,
    get_xva_movers,
    get_xva_sensitivities,
    get_xva_summary,
    search_counterparties,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def build_server(*, auth: bool, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    kwargs: dict[str, Any] = {"instructions": INSTRUCTIONS, "host": host, "port": port, "stateless_http": True}
    if auth:
        from mcp.server.auth.settings import AuthSettings

        from .auth import required_scopes, split_list, verifier_from_env

        env = os.environ
        issuers = split_list(env["XVA_OAUTH_ISSUER"])  # comma-separated; the first is advertised to clients
        kwargs["token_verifier"] = verifier_from_env()
        kwargs["auth"] = AuthSettings(
            issuer_url=issuers[0],
            resource_server_url=env["XVA_RESOURCE_URL"],
            required_scopes=required_scopes(),
            # Entra audiences are app IDs or Application ID URIs, not this server's URL, so the
            # JwtTokenVerifier checks the audience list itself instead of the SDK's URL comparison.
            validate_token_resource=False,
        )
    server = FastMCP("xva-data", **kwargs)
    for fn in TOOLS:
        server.tool(annotations=READ_ONLY)(fn)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="XVA data MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    dev = os.environ.get("XVA_ENV") == "dev"
    if args.transport == "stdio" and not dev:
        raise SystemExit("stdio transport is for development only: set XVA_ENV=dev and use sample data.")
    use_auth = args.transport == "streamable-http" and not dev
    if args.transport == "streamable-http" and dev and args.host != "127.0.0.1":
        raise SystemExit("Unauthenticated dev HTTP must bind to 127.0.0.1.")
    build_server(auth=use_auth, host=args.host, port=args.port).run(transport=args.transport)


if __name__ == "__main__":
    main()
