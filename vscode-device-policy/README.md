# VS Code device policies for desk and exec machines

Deploy with Group Policy / Intune (Windows ADMX from the VS Code `policies` folder), a macOS configuration profile, or `/etc/vscode/policy.json` on Linux (policy names as keys, root-owned, mode 644). Verify on a test machine with **Developer: Policy Diagnostics** before rollout; value types differ by platform.

| Policy | Value for desk and exec users | Why |
| --- | --- | --- |
| `ChatApprovedAccountOrganizations` | the bank's GitHub organization(s) | AI features stay off unless the user signs in with a bank account |
| `ChatStrictPluginOnlyCustomization` | enabled | Ignores user and workspace skills, agents, hooks, instructions and MCP servers; only managed plugins load |
| `ChatAllowManagedHooksOnly` | enabled | Hooks only from managed sources and policy-enabled plugins |
| `ChatToolsAutoApprove` | disabled | No global "approve everything" |
| `ChatToolsTerminalEnableAutoApprove` | disabled | Terminal commands always ask |
| `ChatAgentExtensionTools` | disabled | No tools from third-party VS Code extensions |
| `ChatAgentNetworkFilter` + `ChatAgentAllowedNetworkDomains` | enabled; internal domains only | Agent fetch/browser tools cannot reach the internet |
| `CopilotSessionSync` | disabled (pending InfoSec) | Session data stays on the machine |
| `CopilotOtelEnabled`, `CopilotOtelEndpoint`, `CopilotOtelCaptureContent` | enabled; bank collector; content off | Usage telemetry the user cannot switch off |
| `McpEnterpriseManagedAuthIdp` | bank identity provider | Sign-in to the XVA MCP server through the bank IdP |
| `AllowedExtensions` | `{"github": true, "ms-python": true, "microsoft": true}` (adjust) | Blocks unapproved extensions |

Do not apply `ChatStrictPluginOnlyCustomization` to xva-ai-admins' development machines; they need workspace customizations to author and test before publishing to staging.

Example Linux file (`policy.example.json`) is a starting point only.

Reference: <https://code.visualstudio.com/docs/enterprise/policies>
