# XVA desk Copilot starter kit

Companion to the guide "XVA Desk AI Workflow on GitHub Copilot (VS Code): Setup Guide". Everything here is a template: replace `<org>`, the `*.internal.example` hosts, team names and the dictionary conventions with the bank's own before use. Counterparties and numbers in the sample data are fictional.

| Folder | Becomes | What is in it |
| --- | --- | --- |
| `xva-ai-marketplace/` | The governed plugin marketplace repo | `marketplace.json`; plugins `xva-desk`, `xva-exec`, `xva-dev` (agents, skills, MCP endpoints, audit hooks); Microsoft 365 Copilot agent instructions (`m365/`); shared desk rules; CODEOWNERS; CI validation; fallback-workspace builder |
| `xva-ai-platform/` | The code repo | The eight tools (`xva_mcp/tools.py`) behind three front doors: MCP server, REST API with OpenAPI, command line; landing jobs (`xva_landing`), data dictionary, synthetic sample store, meeting demo, tests, golden questions and scorer |
| `github-private-examples/` | Files for the enterprise `.github-private` repo | Managed settings baseline, per-team plugin files, team mappings |
| `vscode-device-policy/` | Desktop engineering request | VS Code policies for desk and exec machines |

## Quick start (developer machine)

```bash
cd xva-ai-platform
python -m pip install -e ".[dev]"
python sample_data/make_sample_data.py sample_data/xva_store.sqlite
pytest                                   # with mcp + duckdb: 65 passed, 1 skipped (OpenAPI validator, optional)
                                         # without duckdb the DuckDB checks skip; without mcp the server tests skip
python demo/xva_demo.py --pause          # live demo through a real MCP server process (no VS Code needed)

cd ../xva-ai-marketplace
python -m pip install pyyaml pytest
python scripts/validate_customizations.py  # 0 problem(s)
pytest tests                               # 9 tests
python scripts/build_fallback_workspace.py xva-desk ../xva-desk-workspace   # only if managed plugins are unavailable
```

## Store backend: SQLite or DuckDB

The curated store runs on **SQLite by default**. It is built into Python, so there is nothing to install, and it is enough for the sample data and a pilot. **DuckDB** stays available as an option. Nothing outside `src/xva_mcp/db.py` imports a database driver.

| | SQLite (default) | DuckDB (optional) |
| --- | --- | --- |
| Install | Nothing | `pip install -e ".[duckdb]"` (or `pip install duckdb` once your package mirror has it) |
| Store file | `xva_store.sqlite` | `xva_store.duckdb` |
| Chosen by | Any path not ending in `.duckdb`, or `XVA_STORE_BACKEND=sqlite` | A path ending in `.duckdb`, or `XVA_STORE_BACKEND=duckdb` |

To switch to DuckDB later: install the package, rebuild the store with `python sample_data/make_sample_data.py sample_data/xva_store.duckdb`, point `XVA_STORE_PATH` at it and restart the server. `pytest` then also runs the DuckDB checks, including one that both backends give identical tool answers. Keep the SQLite file on the server's local disk, not a network share. For production, add the bank's SQL platform as a third backend in `db.py`.

## One set of tools, three front doors

The eight tools live once, in `src/xva_mcp/tools.py`. Each front door applies the same entitlement checks (executives see desk and book level only), returns the same results with provenance, and writes the same audit line; the line's `via` field says which door was used.

| Front door | Run it | For |
| --- | --- | --- |
| MCP server | `python -m xva_mcp.server` (stdio, dev) or `--transport streamable-http` | GitHub Copilot in VS Code; M365 Copilot MCP plugins |
| REST API + OpenAPI 3.0 | `python -m xva_mcp.rest` (`xva-api`) | M365 Copilot API plugins, Copilot Studio, the bank's own AI platform, scripts |
| Command line | `xva-cli <command>` (`python -m xva_mcp.cli`) | People, scripts, and Copilot agent mode running it in the terminal when MCP servers aren't allowed |

## Demo for meetings (no Copilot needed)

```bat
python demo\xva_demo.py --pause
```

The demo starts the MCP server as its own process, exactly as VS Code does from `mcp.json`, and calls the tools as Copilot would. As an analyst it asks: is the latest date complete, XVA by book, what drove the P&L, top counterparty movers, IR delta by bucket. As an executive it asks for a desk summary (answered), then counterparty movers and a counterparty search (both refused by the server). Finally it prints the audit lines, refusals included. `--pause` waits for Enter between questions. `--store` picks a store (default: the sample DuckDB file if duckdb is installed, else SQLite, else it builds one). `--audit` keeps the audit file.

## Command line

```bat
set XVA_ENV=dev
xva-cli --store sample_data\xva_store.duckdb tools
xva-cli --store sample_data\xva_store.duckdb status --as-of 2026-11-20
xva-cli --store sample_data\xva_store.duckdb summary --as-of 2026-11-19 --level book
xva-cli --store sample_data\xva_store.duckdb pnl --start 2026-11-19 --end 2026-11-20
xva-cli --store sample_data\xva_store.duckdb --role xva-exec movers --start 2026-11-19 --end 2026-11-20   # refused
xva-cli --format json ...                                   # JSON instead of text
```

Text answers end with the source line (system, as-of date, run IDs, unit, sign convention). Exit codes: 0 ok, 2 invalid input or setup, 3 refused, 4 no data, 5 API unreachable.

Reading a store file directly works only with `XVA_ENV=dev`, as for the MCP server. In production the command line goes through the REST API with the user's own token, so the server checks who is asking:

```bat
set XVA_API_URL=https://xva-api.apps.internal.example
set XVA_API_TOKEN=PASTE_ACCESS_TOKEN_HERE
xva-cli summary --level book
```

The token must be for the API's own audience and carry the `xva.read` scope. The Entra admins will say how users get one. One common way is `az account get-access-token --resource API_APP_ID_URI --query accessToken -o tsv`, which works only if the API's app registration allows the Azure CLI as a client. (In `cmd`, don't type `<` or `>`: they redirect.)

### Copilot agent mode with the command line (when MCP servers are blocked)

Copilot's agent mode can run `xva-cli` in the VS Code terminal and read its output. Each command needs the user's approval unless terminal auto-approval is on, and it should stay off. This route carries desk data to the model exactly as MCP would. Present it to InfoSec and the Copilot admins as an option, not as a way around the MCP policy, and check whether the bank also restricts the terminal tool.

To try it in the sandbox, add the environment to `.vscode\settings.json`:

```json
{
  "terminal.integrated.env.windows": {
    "XVA_ENV": "dev",
    "XVA_STORE_PATH": "C:\\dev\\xva-copilot-starter\\xva-ai-platform\\sample_data\\xva_store.duckdb",
    "XVA_AUDIT_LOG": "C:\\dev\\xva-sandbox\\outputs\\audit.jsonl"
  }
}
```

and append to `.github\copilot-instructions.md`:

```markdown
## XVA figures through the command line
When the xva-data tools are not available, get every XVA figure by running `xva-cli` in the terminal. Never compute XVA yourself.
- Run `xva-cli tools` for the commands and `xva-cli <command> -h` for options.
- Start with `xva-cli status --as-of <date>`. If not all sources passed, say so before giving figures.
- Use `xva-cli movers` and `xva-cli pnl` for changes; don't subtract numbers yourself.
- Quote the `Source:` line of each result.
- If the output starts with `Refused:`, tell the user and stop; don't look for the data another way.
```

## REST API and OpenAPI

```bat
set XVA_ENV=dev
set XVA_STORE_PATH=sample_data\xva_store.duckdb
python -m xva_mcp.rest --port 8001
```

Then open `http://127.0.0.1:8001/api/v1/get_xva_summary?as_of=2026-11-19&level=book` or `http://127.0.0.1:8001/openapi.json`. Each tool is `GET /api/v1/<tool name>` with its arguments as query parameters; the OpenAPI `operationId` is the tool name. Errors come back as JSON `{"error", "message"}`: 401 not signed in, 403 refused by entitlements or missing scope, 404 no data, 422 invalid arguments. Only GET is accepted.

Production uses the same sign-in settings as the MCP server (`XVA_OAUTH_ISSUER`, `XVA_OAUTH_AUDIENCE`, `XVA_OAUTH_JWKS_URL`, optional `XVA_ALLOWED_CLIENT_IDS` and `XVA_REQUIRED_SCOPES`), plus `XVA_API_BASE_URL` for the URL published in the OpenAPI description. Run it behind the bank's TLS ingress. Without `XVA_ENV=dev` every request needs a user-delegated bearer token, exactly as for MCP.

Export the description for an API plugin or connector:

```bat
set XVA_API_BASE_URL=https://xva-api.apps.internal.example
set XVA_OAUTH_AUTHORIZE_URL=https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/authorize
set XVA_OAUTH_TOKEN_URL=https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/token
set XVA_OAUTH_SCOPE=api://API_CLIENT_ID/xva.read
python -m xva_mcp.rest --export-openapi openapi.json
```

- **Microsoft 365 Copilot API plugin:** in Microsoft 365 Agents Toolkit, create a declarative agent and add an action from this OpenAPI description. The toolkit generates the plugin manifest. Set up sign-in as an Entra SSO or OAuth registration in the toolkit, and reuse `xva-ai-marketplace/m365/xva-analyst.instructions.md` as the agent's instructions.
- **Copilot Studio:** add the API as a REST API tool or custom connector from the same file. If the import asks for OpenAPI 2.0 (Swagger), convert the file first.
- **The bank's AI platform:** most platforms that support function calling accept an OpenAPI file or a list of operations; the descriptions and parameter documentation are written for a model to read.

## Try the tools in VS Code with sample data

Open `xva-ai-platform` in VS Code and add `.vscode/mcp.json` (development only; never point this at production data):

```json
{
  "servers": {
    "xva-data": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "xva_mcp.server"],
      "env": {
        "XVA_ENV": "dev",
        "XVA_DEV_ROLE": "xva-desk",
        "XVA_STORE_PATH": "${workspaceFolder}/sample_data/xva_store.sqlite",
        "PYTHONPATH": "${workspaceFolder}/src"
      }
    }
  }
}
```

Then copy `xva-ai-marketplace/plugins/xva-desk/skills` to `.github/skills` and the analyst agent to `.github/agents` in a scratch workspace (or run `build_fallback_workspace.py` and swap its `.vscode/mcp.json` for the one above), select **XVA Analyst** in the chat, and ask "What drove XVA P&L on 2026-11-19?". Change `XVA_DEV_ROLE` to `xva-exec` to see the entitlement checks refuse counterparty detail.

## Production deployment of the MCP server

One server serves every front end: VS Code / GitHub Copilot for the desk and super users, and Microsoft 365 Copilot for analysts and executives (see `xva-ai-marketplace/m365/README.md`).

```bash
XVA_STORE_PATH=/data/xva_store.sqlite \
XVA_OAUTH_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0 \
XVA_OAUTH_JWKS_URL=https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys \
XVA_OAUTH_AUDIENCE="<api-client-id>,<m365-sso-application-id-uri>" \
XVA_ALLOWED_CLIENT_IDS="<vscode-client-id>,ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b,<copilot-studio-connector-app-id>" \
XVA_CLIENT_NAMES='{"<vscode-client-id>": "vscode", "<copilot-studio-connector-app-id>": "copilot-studio"}' \
XVA_RESOURCE_URL=https://xva-mcp.apps.internal.example/mcp \
XVA_AUDIT_LOG=/var/log/xva-mcp/audit.jsonl \
python -m xva_mcp.server --transport streamable-http --host 0.0.0.0 --port 8080
```

| Variable | What to put in it |
| --- | --- |
| `XVA_OAUTH_ISSUER` | Comma-separated issuers; the first is advertised to clients. With `accessTokenAcceptedVersion` = 2 on the app, the v2 issuer above. |
| `XVA_OAUTH_AUDIENCE` | Comma-separated audiences: the API's own (for v2 tokens usually its client ID; decode a real token to confirm) and, for Microsoft 365 Copilot SSO, the Application ID URI generated by the SSO registration |
| `XVA_ALLOWED_CLIENT_IDS` | Optional allowlist of front ends: the VS Code client ID seen in real tokens, `ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b` (Microsoft 365 Copilot token store), the Copilot Studio connector's app ID |
| `XVA_ROLE_MAP` | Only if your role names differ: JSON mapping Entra group IDs or app-role values to `xva-desk`, `xva-exec`, `xva-admin`. Prefer Entra app roles with exactly those values. |
| `XVA_CLIENT_NAMES` | Optional JSON naming client IDs in the audit log |

The server accepts only user-delegated tokens (with `scp` and the user's `oid`); an agent or connector calling as itself is refused, so entitlements always follow the signed-in person. Run it behind the bank's TLS ingress, with a read-only identity on the curated store and the audit log shipped to the SIEM. If Microsoft 365 Copilot is a front end, Microsoft's cloud must reach the server, so agree the route (for example Azure API Management or a private endpoint) with the network team. The server refuses to start over stdio unless `XVA_ENV=dev`, and refuses unauthenticated HTTP except on 127.0.0.1 in dev.

## Golden questions for each front end

- VS Code / GitHub Copilot: `evals/run_with_copilot_cli.sh` runs and scores them.
- Microsoft 365 Copilot: ask each question in the agent, save the answer as `evals/out-m365/<id>.md`, then run `python evals/score_answer.py evals/golden_questions.yaml evals/out-m365`.

## Known gaps to close before production

- Replace the weekday calendar in the sample with the bank's holiday calendar (Toronto).
- Move the curated store to the bank's SQL platform: add a backend in `db.py` (SQLite and DuckDB are already there); `store.py` keeps the same queries.
- Agree the sign convention, units, thresholds and P&L driver names in `dictionary.yaml` with the desk and Product Control.
- Fill in `data-owners.md`, CODEOWNERS teams and the hook/endpoint hosts.
- Grow `evals/golden_questions.yaml` to 50+ real desk questions.
- Put the REST API behind the bank's API gateway (for example Azure API Management) for TLS, throttling and the network route from Microsoft's cloud; the API itself has no rate limiting.
