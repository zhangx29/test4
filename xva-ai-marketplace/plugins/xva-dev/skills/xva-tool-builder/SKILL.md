---
name: xva-tool-builder
description: Checklist and steps for adding or changing an xva-data MCP tool safely - typed inputs, read-only annotation, provenance, entitlement rule, audit, tests, golden questions, agent tool lists and the pull request. Use whenever a tool in xva-ai-platform is added, renamed or changed.
argument-hint: The tool to add or change, e.g. "get_trade_activity from the Calypso extract"
disable-model-invocation: true
---

# Adding or changing an xva-data tool

Work through [the checklist](references/tool-checklist.md) in order and tick each item in the pull request description.

1. **Contract first.** Write the Pydantic output model in `src/xva_mcp/models.py`, with a `provenance` field. Inputs use enums, date types and bounded integers; no free-text filters that reach SQL.
2. **Store function.** Add a parameterised query in `src/xva_mcp/store.py`. Grouping and column expressions come from fixed dictionaries keyed by validated enums.
3. **Tool wrapper.** Add the function in `src/xva_mcp/server.py`, route it through `_guarded(...)`, add it to `TOOLS`, and write a docstring that tells the model when to use it and what the fields mean.
4. **Entitlements.** Add the tool to `TOOL_ROLES` in `src/xva_mcp/entitlements.py`. Decide whether executives may call it, and at which level.
5. **Tests.** Cover: output ties to an independent query, row cap, unknown date, each role's access, audit line written.
6. **Golden questions.** Add at least three to `evals/golden_questions.yaml`, including one the tool must refuse.
7. **Agents and skills.** Agents use `xva-data/*`, so new tools appear automatically; update skills that should call the tool, and the dictionary if new terms appear.
8. **Run** `pytest` in xva-ai-platform and `python scripts/validate_customizations.py` in xva-ai-marketplace. Open the pull request; code owners and security review `mcp.json` changes.

Breaking change to a tool's inputs or outputs: add a new tool name, keep the old one for one release, then remove it.
