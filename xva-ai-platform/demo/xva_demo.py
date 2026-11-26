"""Live demo of the XVA data tools, called through MCP the way GitHub Copilot calls them.

It starts the real MCP server as a separate process (exactly as VS Code does from mcp.json), asks a set
of desk questions as an XVA analyst, then as an executive (to show counterparty detail being refused),
and finishes with the audit lines the server wrote. No Copilot or VS Code needed, so it works while the
bank's MCP policy is still pending. Uses development mode and fictional sample data only.

    python demo\\xva_demo.py                      # sample store, runs straight through
    python demo\\xva_demo.py --pause              # wait for Enter between questions (for meetings)
    python demo\\xva_demo.py --store sample_data\\xva_store.duckdb --audit outputs\\demo_audit.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):  # runs from a checkout even before `pip install -e .`
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from xva_mcp import db, store, tools  # noqa: E402
from xva_mcp.render import render  # noqa: E402

WIDTH = 100


def say(text: str = "") -> None:
    print(text, flush=True)


def banner(text: str) -> None:
    say()
    say("=" * WIDTH)
    say(text)
    say("=" * WIDTH)


def indent(text: str) -> str:
    return textwrap.indent(text, "    ")


def pick_store(explicit: str | None, workdir: Path) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"Store not found: {path}")
        return path
    candidates = [ROOT / "sample_data" / "xva_store.sqlite"]
    if db.duckdb_available():
        candidates.insert(0, ROOT / "sample_data" / "xva_store.duckdb")
    for path in candidates:
        if path.exists():
            return path
    from sample_data.make_sample_data import build

    say("No sample store found; building one (fictional data)...")
    return build(workdir / "xva_store.sqlite")


def server_params(store_path: Path, audit_path: Path, role: str) -> StdioServerParameters:
    env = {
        **os.environ,
        "XVA_ENV": "dev",
        "XVA_DEV_ROLE": role,
        "XVA_DEV_USER": f"demo-{role.split('-')[-1]}",
        "XVA_STORE_PATH": str(store_path),
        "XVA_AUDIT_LOG": str(audit_path),
        "PYTHONPATH": os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
    }
    return StdioServerParameters(command=sys.executable, args=["-m", "xva_mcp.server"], env=env)


class Demo:
    def __init__(self, pause: bool):
        self.pause = pause
        self.step = 0

    def wait(self) -> None:
        if self.pause:
            input("\n    [Enter] for the next question ")

    async def ask(self, session: ClientSession, question: str, tool: str, args: dict[str, Any]) -> None:
        self.step += 1
        banner(f"{self.step}. \"{question}\"")
        shown = ", ".join(f"{k}={v}" for k, v in args.items())
        say(f"    Tool call: {tool}({shown})")
        say()
        result = await session.call_tool(tool, args)
        if result.isError:
            text = result.content[0].text if result.content else "error"
            text = text.split(": ", 1)[1] if text.startswith("Error executing tool") else text
            say(f"    REFUSED by the server: {text}")
        else:
            model = tools.result_model(tool).model_validate(result.structuredContent)
            say(indent(render(model)))
        self.wait()


async def run(store_path: Path, audit_path: Path, server_log: Path, pause: bool) -> None:
    os.environ["XVA_STORE_PATH"] = str(store_path)  # only to pick dates that exist in this store
    dates = store.business_dates()
    if len(dates) < 2:
        raise SystemExit("The store needs at least two business dates for the demo.")
    start, end = dates[-2].isoformat(), dates[-1].isoformat()
    demo = Demo(pause)

    with open(server_log, "a", encoding="utf-8") as errlog:
        banner("Part 1 - XVA analyst (role xva-desk)")
        async with stdio_client(server_params(store_path, audit_path, "xva-desk"), errlog=errlog) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = (await session.list_tools()).tools
                say(f"    Server xva-data offers {len(listed)} tools, all read-only:")
                for t in listed:
                    ro = "read-only" if t.annotations and t.annotations.readOnlyHint else "CHECK"
                    say(f"      - {t.name:<34} {ro}")
                demo.wait()
                await demo.ask(session, f"Is the data for {end} complete?", "get_data_status", {"as_of": end})
                await demo.ask(session, f"Show me XVA by book for {start}", "get_xva_summary",
                               {"as_of": start, "level": "book"})
                await demo.ask(session, f"What drove XVA P&L from {start} to {end}?", "get_pnl_attribution",
                               {"start": start, "end": end, "level": "desk"})
                await demo.ask(session, "Which counterparties moved the most?", "get_xva_movers",
                               {"start": start, "end": end, "level": "counterparty", "top_n": 5})
                await demo.ask(session, "What is our IR delta by tenor bucket?", "get_xva_sensitivities",
                               {"asset_class": "IR", "measure": "delta", "group_by": "bucket", "as_of": start,
                                "top_n": 8})

        banner("Part 2 - Executive (role xva-exec): desk, entity and book level only")
        async with stdio_client(server_params(store_path, audit_path, "xva-exec"), errlog=errlog) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                await demo.ask(session, f"Give me XVA by desk for {start}", "get_xva_summary",
                               {"as_of": start, "level": "desk"})
                await demo.ask(session, "How has desk XVA moved this month?", "get_xva_history",
                               {"start": dates[-1].replace(day=1).isoformat(), "end": end})
                await demo.ask(session, "Which counterparties moved the most?", "get_xva_movers",
                               {"start": start, "end": end, "level": "counterparty", "top_n": 5})
                await demo.ask(session, "Find the Maple Leaf counterparty", "search_counterparties",
                               {"query": "Maple"})

    banner("Part 3 - Audit log: one line per tool call, including refusals")
    lines = [json.loads(x) for x in audit_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    say(f"    {len(lines)} lines in {audit_path}")
    say()
    for rec in lines[-demo.step:]:
        outcome = rec.get("outcome", "")
        outcome = "denied" if outcome.startswith("denied") else outcome
        say(f"    {rec.get('ts', '')[:19]}  {rec.get('user', ''):<12} {rec.get('role', ''):<9} "
            f"via={rec.get('via', '')}  {rec.get('tool', ''):<32} {outcome:<7} rows={rec.get('rows') or '-'}")


def main() -> None:
    p = argparse.ArgumentParser(description="Demo of the XVA data tools through MCP (development mode)")
    p.add_argument("--store", help="store file (default: sample_data/xva_store.duckdb or .sqlite)")
    p.add_argument("--audit", help="audit log to write (default: a temporary file)")
    p.add_argument("--pause", action="store_true", help="wait for Enter between questions")
    a = p.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="xva-demo-"))
    store_path = pick_store(a.store, workdir)
    audit_path = Path(a.audit) if a.audit else workdir / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    server_log = workdir / "server.log"

    say("XVA data tools - live demo")
    say("The MCP server runs as its own process and is called exactly as GitHub Copilot calls it.")
    say(f"Store: {store_path} (development mode, fictional sample data)")
    try:
        asyncio.run(run(store_path, audit_path, server_log, a.pause))
    except Exception as exc:
        say(f"\nThe demo stopped: {type(exc).__name__}: {exc}")
        say(f"Server messages are in {server_log}")
        raise SystemExit(1)
    say()
    say(f"Done. Server messages: {server_log}")


if __name__ == "__main__":
    main()
