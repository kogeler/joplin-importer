"""Microsoft Graph delegated authentication.

Public-client device-code flow with the least-privileged ``Notes.Read`` scope.
No client secret is required or stored; the access token lives only in memory
wrapped in :class:`Secret`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from ...secretstore import Secret

GRAPH_SCOPES = ["Notes.Read"]
DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"


class GraphAuthError(RuntimeError):
    pass


def acquire_token_device_code(
    client_id: str,
    *,
    authority: str = DEFAULT_AUTHORITY,
    prompt: Callable[[str], None] | None = None,
) -> tuple[Secret, str]:
    """Run the device-code flow; returns (access token, account label)."""
    import msal

    show = prompt or (lambda message: print(message, file=sys.stderr))  # noqa: T201
    app = msal.PublicClientApplication(client_id, authority=authority)
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        raise GraphAuthError(
            f"device flow could not be started: {flow.get('error_description', flow)}"
        )
    show(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise GraphAuthError(
            f"authentication failed: {result.get('error_description', result.get('error'))}"
        )
    account = ""
    claims = result.get("id_token_claims") or {}
    if claims:
        account = claims.get("preferred_username") or claims.get("oid") or ""
    return Secret(result["access_token"]), account
