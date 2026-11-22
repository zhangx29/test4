# Enterprise managed settings (examples)

These files belong in the enterprise's designated `.github-private` repository, owned by the enterprise platform team, not by the desk. They are examples to discuss with that team.

| File | Applies to | Purpose |
| --- | --- | --- |
| `copilot/managed-settings.json` | Every Copilot Business/Enterprise user in the enterprise | Baseline: no bypass mode, approved marketplaces, MCP allowlist, telemetry to the bank's collector |
| `copilot/teams/xva-desk.json` | Enterprise team `xva-desk` | Adds the XVA marketplace and force-enables `xva-desk` |
| `copilot/teams/xva-exec.json` | Enterprise team `xva-exec` | Force-enables `xva-exec` only |
| `copilot/teams/xva-ai-admins.json` | Enterprise team `xva-ai-admins` | All three plugins plus the staging marketplace for testing |
| `copilot/team-mappings.json` | n/a | Maps each team file to enterprise team slugs |

Before committing:

1. `strictKnownMarketplaces` is enterprise-wide and disables the built-in marketplaces. List every marketplace the bank approves, not only the XVA one shown here.
2. `allowedMcpServers` is enterprise-wide too. Merge the XVA endpoints into the bank's existing allowlist; anything not matched is blocked.
3. `permissions` in this file apply to everyone, so keep them bank-wide. Desk-only rules, for example denying `Edit(/.github/**)` and `Edit(/.vscode/**)` for `xva-desk`, need the permission keys marked overridable in the enterprise file and the rules placed in the team file (see the GitHub reference for the syntax).
4. `telemetry.capture` decides what leaves the client: keep prompts, responses and tool output off unless InfoSec approves.
5. After committing, check **Enterprise → AI controls → Agents → Copilot settings validation**. Clients pick up changes within about an hour, or on restart.

Reference: <https://docs.github.com/en/copilot/reference/enterprise-administrators/enterprise-managed-settings>
