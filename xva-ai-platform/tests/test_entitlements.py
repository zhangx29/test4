import json
import os

from xva_mcp import entitlements


def test_exec_sees_desk_and_book_only(call, monkeypatch):
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    assert not call("get_xva_summary", {"as_of": "2026-11-19", "level": "book"}).isError
    denied = call("get_xva_summary", {"as_of": "2026-11-19", "level": "counterparty"})
    assert denied.isError and "desk or book" in denied.content[0].text


def test_exec_cannot_reach_counterparty_detail_by_other_routes(call, monkeypatch):
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    assert call("search_counterparties", {"query": "north"}).isError
    assert call("get_counterparty_xva_timeseries", {"counterparty_id": "CP001", "start": "2026-11-02", "end": "2026-11-13"}).isError
    assert call("get_pnl_attribution", {"start": "2026-11-18", "end": "2026-11-19", "level": "counterparty", "key": "CP001"}).isError
    assert call("get_xva_sensitivities", {"asset_class": "CR", "measure": "cs01", "group_by": "counterparty"}).isError
    assert call("get_xva_movers", {"start": "2026-11-18", "end": "2026-11-19", "level": "counterparty"}).isError
    assert not call("get_pnl_attribution", {"start": "2026-11-18", "end": "2026-11-19", "level": "book", "key": "XVA-FIN"}).isError


def test_no_identity_outside_dev_is_refused_and_audited(call, monkeypatch):
    monkeypatch.delenv("XVA_ENV")
    result = call("get_data_status", {})
    assert result.isError and "No authenticated identity" in result.content[0].text
    last = json.loads(open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8").read().splitlines()[-1])
    assert last["user"] == "unknown" and last["outcome"].startswith("denied")


def test_denials_are_audited_with_the_user(call, monkeypatch):
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    call("search_counterparties", {"query": "north"})
    last = json.loads(open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8").read().splitlines()[-1])
    assert last["user"] == "test-user" and last["outcome"].startswith("denied")


def test_group_mapping_picks_highest_role(monkeypatch):
    monkeypatch.setenv("XVA_ROLE_MAP", json.dumps({"sg-xva-desk": "xva-desk", "sg-xva-exec": "xva-exec"}))
    assert entitlements.role_from_groups(["sg-xva-exec", "sg-xva-desk"]) == "xva-desk"
    assert entitlements.role_from_groups(["sg-other"]) is None


# ---------------------------------------------------------------- signed-in users (HTTP mode)

M365_CLIENT = "ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b"


def signed_in(monkeypatch, claims, client_id=M365_CLIENT):
    """Simulate the auth middleware having validated a token for this request."""
    from mcp.server.auth.middleware import auth_context
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(
        token="t",
        client_id=client_id,
        scopes=str(claims.get("scp", "")).split(),
        subject=claims.get("oid"),
        claims=claims,
    )
    monkeypatch.setattr(auth_context, "get_access_token", lambda: token)
    monkeypatch.delenv("XVA_ENV")  # the dev identity must not be used once a token is present


def test_m365_exec_token_gets_exec_view_and_client_is_audited(call, monkeypatch):
    signed_in(monkeypatch, {"oid": "exec-1", "scp": "xva.read", "roles": ["xva-exec"]})
    assert not call("get_xva_summary", {"as_of": "2026-11-19", "level": "book"}).isError
    assert call("get_xva_summary", {"as_of": "2026-11-19", "level": "counterparty"}).isError
    last = json.loads(open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8").read().splitlines()[-1])
    assert last["user"] == "exec-1" and last["role"] == "xva-exec" and last["client"] == "m365-copilot"


def test_groups_and_app_roles_are_combined(monkeypatch):
    signed_in(
        monkeypatch, {"oid": "u-2", "scp": "xva.read", "groups": ["sg-unrelated"], "roles": ["xva-desk"]}, "vscode"
    )
    user = entitlements.current_user()
    assert user.role == "xva-desk" and user.client_id == "vscode"


def test_app_only_tokens_are_refused(call, monkeypatch):
    # An agent or connector acting as itself: no delegated scope, no user object ID.
    signed_in(monkeypatch, {"roles": ["xva-desk"]}, "copilot-studio-agent")
    result = call("get_data_status", {})
    assert result.isError and "signed-in user" in result.content[0].text


def test_unknown_user_is_refused(call, monkeypatch):
    signed_in(monkeypatch, {"oid": "u-3", "scp": "xva.read", "roles": ["some-other-app-role"]})
    result = call("get_data_status", {})
    assert result.isError and "not in an XVA AI group" in result.content[0].text
