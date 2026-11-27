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
pytest                                   # with mcp + duckdb: 91 passed, 1 skipped (OpenAPI validator, optional)
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
    "XVA_STORE_PATH": "C:\\BMOdev\\xva-copilot-starter\\xva-ai-platform\\sample_data\\xva_store.duckdb",
    "XVA_AUDIT_LOG": "C:\\BMOdev\\xva-sandbox\\outputs\\audit.jsonl"
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

## Real data: landing your extracts

The tools read four tables: counterparties, XVA values, sensitivities and P&L explain. `xva_landing.land_extract` loads them from CSV or Excel files with your own column names, mapped in `landing/landing_config.yaml`. Wide files (one column per measure or driver), label translation and thousands scaling are configured there too. Copy `landing/landing_config.example.yaml` to start. `landing/templates/` holds example files in the store's own layout.

```bat
python -m xva_landing.schema --store C:\xva_data\store\xva_store.duckdb
python -m xva_landing.land_extract --config landing\landing_config.yaml --store C:\xva_data\store\xva_store.duckdb ^
  counterparties --file C:\xva_data\inbox\2026-11-20\counterparties.csv
python -m xva_landing.land_extract --config landing\landing_config.yaml --store C:\xva_data\store\xva_store.duckdb ^
  --archive C:\xva_data\archive xva --as-of 2026-11-20 --values ...\xva_values.csv --sens ...\sensitivities.csv ^
  --pnl ...\pnl_explain.csv --control values=<total from the official report>
```

Checks before anything is written: required columns, dates match `--as-of`, numbers parse, no Excel errors, known measures, drivers and sensitivity types, every counterparty in the counterparty table, no duplicate rows, and optional control totals. A rejected file loads nothing and its reasons appear in `status`. Exit codes: 0 loaded, 1 rejected, 2 setup problem.

Each date has one official run per source, recorded in `data_status`; the tools read only that run. Re-running the same files is harmless. Loading new files for a date is a restatement: the new run becomes official, and the old rows stay for audit. A restatement that fails its checks leaves the old run official and records the rejection. `--archive` keeps a copy of every input file with its hash, and `landing_log` records who loaded what.

Set `expected_sources` and `official_source` in `src/xva_mcp/dictionary.yaml` to the sources you actually land, or every date will show sources as missing. Stored procedures: add them to `ALLOWED` in `land_stored_procs.py` and export with `python -m xva_landing.land_stored_procs --proc ... --param as_of=... --out file.csv` (needs `pyodbc` and a read-only login in `XVA_SQL_CONN`). `landing/daily_load.example.bat` chains the daily steps for Task Scheduler. The desk's MFL procedures (the LITES templates notebook route) are covered in "Stored procedures or CSV files" below.

Limitation: books come from each counterparty's book in the counterparty table. If a counterparty trades in several books, book totals need a netting-set-to-book mapping, which is a later change.

## The desk's daily XVA CSV files

`xva_landing.load_desk_csv` loads the files in the desk's XVA CSV folder one business date at a time. `landing/desk_csv.yaml` describes them (folder, file names, columns), and `landing/desk_dictionary.yaml` defines the desk's components and sensitivities.

| File | What is loaded | Checks |
| --- | --- | --- |
| `XVA_CVA_CVACO_DVACO_FVA_by_Cpty_yyyymmdd` (required) | CVA, CVACO, DVACO (reported as DVA), FVA per legal entity and counterparty | COB_DATE = file date; numbers; no duplicate entity/counterparty; XVA = CVA + DVACO + FVA on every row |
| `CVA_Sens`, `FVA_Sens`, `XVA_Sens_yyyymmdd` | Per component (CVA, FVA, XVA): IR PV01 by tenor (`pv01`) and parallel (`pv01_parallel`, incl. CORRASOFR basis), IR vega; FX spot delta, vega, up/down gamma and gamma cross terms; commodity deltas by balance-of-year bucket and parallel (AECO, NG incl. LAG, OIL, NWROC, WCS, NGL, NBP, AL, CA), precious metals; EQ delta; `CARRY_1D` | Every column matches a rule; column totals = the `_Cons_` file; XVA = CVA + FVA per column |
| `XVA_ALL01_yyyymmdd` | Credit spread CS01 (BMO cash, BMO CDS, counterparty CDS) by tenor and parallel; `CPTY_CR01_1pct`; counterparty RATING, CREDIT_CURVE, CUSTOMER_TYPE, CPTY_TYPE (shown by `search_counterparties`); the spread curve MTH6–MTH600 (table `xva_cpty_curve`) | Every column matches a rule |
| `XVA_CVA_CVACO_DVACO_FVA_SSA_yyyymmdd` | Tags those counterparties with segment SSA | |
| `XVA_CVA_CVACO_DVACO_FVA_<start>_yyyymmdd` | Desk totals for every date in the file (for `get_xva_history`) | XVA = CVA + DVACO + FVA; the day's total = the counterparty file |

Values files and column rules must pass, or the day is rejected and nothing is loaded. Tie-outs against the control, history and sum files are reported in `status` notes; set `strict_tieouts: true` to make them reject the day. Counterparties are added automatically with their code as the name, until a counterparty static file is landed with `land_extract counterparties`. Zero sensitivities aren't stored. Use DuckDB for this volume.

```bat
cd C:\BMOdev\xva-copilot-starter\xva-ai-platform
set XVA_DICTIONARY_PATH=%CD%\landing\desk_dictionary.yaml
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %USERPROFILE%\xva_data\store\xva_store.duckdb --list
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %USERPROFILE%\xva_data\store\xva_store.duckdb --as-of 2026-11-24
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %USERPROFILE%\xva_data\store\xva_store.duckdb --all --archive %USERPROFILE%\xva_data\archive
```

`--from 2026-11-01 --to 2026-11-24` loads a range, `--reload` reloads dates already loaded, and `--values-only` loads a day whose sensitivity files are broken. `landing/daily_load_desk.example.bat` is the daily job. Everything that reads this store (command line, MCP server, REST API, the VS Code terminal) needs `XVA_DICTIONARY_PATH` set to `landing\desk_dictionary.yaml`. To try it without real data: `python sample_data\make_desk_files.py C:\temp\xva_csv_sample 2026-11-23 2026-11-24` writes fictional files in the same layout, then add `--folder C:\temp\xva_csv_sample`.

New in the tools: an `entity` level (legal entity) for summaries, movers and P&L; `component` on `get_xva_sensitivities` (XVA, CVA or FVA); and `get_xva_history` for desk totals over time (`xva-cli history`). Totals add only the components marked `in_total` in the dictionary.

## Stored procedures or CSV files: choosing the source

Each dataset in `landing/desk_csv.yaml` (`values`, `sensitivities`, `credit_spreads`, `ssa`, `history`, `pnl_explain`) has a `source`:

| `source` | What the loader does |
| --- | --- |
| `csv` (default) | reads the file from the CSV folder, as before (stored procedure output saved earlier) |
| `sp` | runs the stored procedure named by the dataset's `procedure` through MFL, saves the result as CSV in `sp_output_folder\yyyymmdd\`, then loads that file with the same checks |
| `off` | skips the dataset |

The command line overrides the file for one run: `--source pnl_explain=csv`, `--source all=csv`, and several `--source` flags together. The first line of the output says which source each dataset used.

`landing/stored_procs.yaml` is the allowlist of procedures: only the procedures listed there can run. Each entry has `db`, `sp` and `params`, and runs as `pymfl.DbUtils().Query(db, sp, names, values, None)`, the same way as the desk's LITES templates notebook. Parameter values can be `'{start}'`, `'{end}'`, `'{as_of}'` (the date being loaded) and `'{cpty_codes}'` (null = all); anything else is passed as written. The file comes with the P&L template procedure (`cva_pnl`, `fva_pnl` and `xva_pnl`, for `P_XVAType` CVA, FVA and XVA) and the CR01 template (`fva01`, which can be run but isn't loaded into the store yet). `backend: python` with `callable: module:function` uses your own function in place of MFL.

P&L explain comes from the procedures (`pnl_explain: source: sp`) and is optional (`required: false`): if a procedure fails, the day still loads without P&L and `status` says why. The values and sensitivities stay on CSV until you switch them.

```bat
rem 1. look at what a procedure returns (needs the Python where "import pymfl_setup" works)
python -m xva_landing.sp_runner --config landing\desk_csv.yaml --list
python -m xva_landing.sp_runner --config landing\desk_csv.yaml --proc fva_pnl --as-of t-1 --show 10
rem 2. map its columns in desk_csv.yaml -> pnl_explain (done for the P&L template; needed for a new procedure)
rem 3. load a day; t-1 is the previous TOR business day
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %USERPROFILE%\xva_data\store\xva_store.duckdb --as-of t-1
rem    the same day from CSV files saved earlier, without running anything
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %USERPROFILE%\xva_data\store\xva_store.duckdb --as-of 2026-11-24 --source all=csv
```

The P&L template procedure returns one row per legal entity and counterparty and one column per driver, and `pnl_explain` is set up for that layout (`driver_columns: all`). Drivers keep the procedure's column names, except `Unexplained P/L`, stored as `unexplained`. `ACTUAL_PL` and `Total Expected P/L` are stored apart in `xva_pnl_actual`, and every row is tied out: Total Expected P/L should equal the drivers except `TEMPORARY_*` and Unexplained P/L, and ACTUAL_PL should equal all of them. A break is a note in `status`; `strict_tieouts: true` rejects the P&L instead. `get_pnl_attribution` (`xva-cli pnl --component XVA|CVA|FVA`, XVA by default) returns the actual P&L, the driver groups from `pnl_driver_groups` in the dictionary, and the drivers, with zero drivers left out. A procedure with one row per driver instead uses `driver_column` and `value_column` (the long layout).

To add another procedure later: add an entry in `stored_procs.yaml`, look at its columns with `--show`, then set `procedure:` and `source: sp` on the dataset it feeds (`values`, `credit_spreads`, `ssa`, `history`; for sensitivities, `procedure` and `control_procedure` on each file). A procedure that returns something new, such as FVA01, needs a dataset and a table first.

## Counterparty variants (XVATEST)

Test calculations of a counterparty, such as `XVATEST-CHT` and `XVATEST-CHT2` next to `CHT`, load every day next to the official data and can be compared in the chat, but never reach a total.

- **Which variants exist:** `landing/variants.yaml` lists each variant's `code`, the official counterparty it belongs to (`base`), what it calculates differently (`method`, quoted in answers) and its `owner`. Only listed codes load.
- **How they load:** the `variants` section of `landing/desk_csv.yaml` names the datasets to load for them (`values`, `sensitivities`, `credit_spreads`, `pnl_explain`), each through that dataset's own procedure(s). On a variant call, `P_CPTY_CODE` gets the variant codes and `params` replaces `P_ExcludedCpty` with a list without XVATEST. Results are saved as `VARIANTS_<usual name>` in `sp_output_folder`; `source: csv` reads those names from the CSV folder instead. As shipped only `pnl_explain` is listed, since it is the only dataset with procedures; add the others once their sections have `procedure:`.
- **Kept apart:** variant rows go to their own tables (`xva_variant_value`, `xva_variant_sensitivity`, `xva_variant_pnl_explain`, `xva_variant_pnl_actual`, plus `dim_counterparty_variant`). No official query reads them, and official loads drop any row whose code contains `marker` (XVATEST) with a note. Only the variants are stored, not copies of other counterparties. A variant problem is a note under the `xva_variants` source in `status` and never fails the official day.
- **In the chat:** `compare_counterparty_variants` (`xva-cli variants --counterparty-id CHT`) shows the official counterparty and its variants side by side: balances with each variant's difference, sensitivities by asset class and the risk factors that differ most, P&L by driver group with actual P&L, and what's not loaded. Desk and admins only; executives are refused.

```bat
rem daily: variants load after each official day
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %STORE% --as-of t-1
rem variants only, e.g. after adding one to variants.yaml
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %STORE% --from 2026-11-01 --to t-1 --variants-only --reload
rem new XVATEST codes the procedures return that variants.yaml doesn't list (nothing is loaded)
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %STORE% --as-of t-1 --find-variants
rem official data only, for one run
python -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store %STORE% --as-of t-1 --source variants=off
```

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
