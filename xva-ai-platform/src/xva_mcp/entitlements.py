"""Who is calling, and what they may see.

Production (HTTP): identity comes from the OAuth access token validated by the server
(see auth.py); the role comes from a group claim mapped through XVA_ROLE_MAP. The MCP server
and the REST API validate tokens the same way and apply the same checks below.
Development (stdio): identity comes from XVA_DEV_USER / XVA_DEV_ROLE, and only when
XVA_ENV=dev, so a misconfigured production server can never fall back to it.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

ROLES = ("xva-admin", "xva-desk", "xva-exec")

# Tool -> roles allowed to call it.
TOOL_ROLES: dict[str, set[str]] = {
    "get_data_status": {"xva-admin", "xva-desk", "xva-exec"},
    "get_definition": {"xva-admin", "xva-desk", "xva-exec"},
    "get_xva_summary": {"xva-admin", "xva-desk", "xva-exec"},
    "get_xva_movers": {"xva-admin", "xva-desk", "xva-exec"},
    "get_pnl_attribution": {"xva-admin", "xva-desk", "xva-exec"},
    "get_xva_sensitivities": {"xva-admin", "xva-desk", "xva-exec"},
    "get_xva_history": {"xva-admin", "xva-desk", "xva-exec"},
    "get_counterparty_xva_timeseries": {"xva-admin", "xva-desk"},
    "search_counterparties": {"xva-admin", "xva-desk"},
    "compare_counterparty_variants": {"xva-admin", "xva-desk"},  # test variants: not for executives
}

# Roles that may see counterparty-level detail.
COUNTERPARTY_DETAIL_ROLES = {"xva-admin", "xva-desk"}


# Set by the REST API (rest.py) for the duration of a request, after it has validated the bearer token.
# The MCP server uses the SDK's own auth context instead.
request_token: ContextVar[Any] = ContextVar("xva_request_token", default=None)


class AccessDenied(PermissionError):
    """Raised when the caller's role does not allow the request. The message is shown to the agent."""


@dataclass(frozen=True)
class User:
    user_id: str
    role: str
    client_id: str | None = None  # which front end obtained the token (VS Code, M365 Copilot, ...)


def _role_map() -> dict[str, str]:
    """Map identity-provider group names or IDs to roles. Override with XVA_ROLE_MAP (JSON)."""
    default = {role: role for role in ROLES}
    raw = os.environ.get("XVA_ROLE_MAP")
    return {**default, **json.loads(raw)} if raw else default


def role_from_groups(groups: list[str]) -> str | None:
    """Highest-privilege role among the caller's groups; None if no group maps to a role."""
    mapping = _role_map()
    roles = {mapping[g] for g in groups if g in mapping}
    for role in ROLES:  # ROLES is ordered from most to least privileged
        if role in roles:
            return role
    return None


def current_user() -> User:
    """Resolve the caller. Raises AccessDenied if no trustworthy identity is available."""
    token = request_token.get()
    if token is None:
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token

            token = get_access_token()
        except Exception:  # pragma: no cover - mcp not installed, or no MCP request in progress
            token = None

    if token is not None:
        claims = token.claims or {}
        # Only user-delegated tokens: they carry delegated scopes (scp) and the user's object ID.
        # App-only tokens (an agent or connector acting as itself) would make every caller look the same.
        if not claims.get("scp") or not token.subject:
            raise AccessDenied("This server accepts only tokens issued for a signed-in user.")
        groups = [*(claims.get("groups") or []), *(claims.get("roles") or [])]
        role = role_from_groups(groups)
        if role is None:
            raise AccessDenied("Your account is not in an XVA AI group. Ask an xva-ai-admin for access.")
        return User(user_id=token.subject, role=role, client_id=token.client_id or None)

    if os.environ.get("XVA_ENV") == "dev":
        role = os.environ.get("XVA_DEV_ROLE", "xva-desk")
        if role not in ROLES:
            raise AccessDenied(f"Unknown dev role {role!r}")
        return User(user_id=os.environ.get("XVA_DEV_USER", "dev-user"), role=role, client_id="dev")

    raise AccessDenied("No authenticated identity on this request.")


def require_tool(user: User, tool: str) -> None:
    if user.role not in TOOL_ROLES.get(tool, set()):
        raise AccessDenied(f"Role {user.role} may not call {tool}.")


def require_level(user: User, level: str, counterparty_id: str | None = None) -> None:
    """Executives see desk, legal entity and book aggregates only."""
    wants_detail = level == "counterparty" or counterparty_id is not None
    if wants_detail and user.role not in COUNTERPARTY_DETAIL_ROLES:
        raise AccessDenied(
            "Counterparty-level detail is not available for your role. Ask for desk, entity or book level instead."
        )
