"""Sync the user's local Code-managed Mionika OAuth tokens up to the VPS.

Mionika uses interactive OAuth (PKCE, no client_credentials grant). Code has
already done the auth_code flow and stored tokens in ~/.claude/.credentials.json.
We piggyback: read tokens locally, POST them to the server's
`/admin/users/:id/mionika-tokens` so the classifier can authenticate as this
user when calling Mionika MCP.

Re-run after Code re-auths Mionika (e.g. after refresh-token revocation,
~once every 1-2 months in practice).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx

from ...config import load_config


def _read_local_mionika_tokens() -> dict:
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.is_file():
        raise click.ClickException(
            f"Code credentials not found at {cred_path}. "
            "Authenticate Mionika MCP in Claude Code first."
        )
    data = json.loads(cred_path.read_text(encoding="utf-8"))
    mcp_oauth = data.get("mcpOAuth") or {}
    mio_entries = [v for k, v in mcp_oauth.items() if k.startswith("mionika|")]
    if not mio_entries:
        raise click.ClickException(
            "No mionika OAuth entry in ~/.claude/.credentials.json. "
            "Authenticate Mionika MCP in Claude Code first."
        )
    if len(mio_entries) > 1:
        click.echo(
            f"Warning: {len(mio_entries)} mionika entries found; using the first.",
            err=True,
        )
    entry = mio_entries[0]
    discovery = entry.get("discoveryState") or {}
    auth_server = discovery.get(
        "authorizationServerUrl", "https://mionika.ryefield.com/"
    )
    token_endpoint = auth_server.rstrip("/") + "/oauth/token"
    return {
        "access_token": entry["accessToken"],
        "refresh_token": entry["refreshToken"],
        "expires_at": entry["expiresAt"],
        "client_id": entry["clientId"],
        "token_endpoint": token_endpoint,
    }


@click.command(name="sync-mionika-tokens")
@click.option("--user-id", default=None,
              help="Target user (default: this CLI's user_id from config).")
def sync_mionika_tokens(user_id: str | None):
    """Push local Code-managed Mionika OAuth tokens to the server."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")

    target = user_id or cfg.user_id
    payload = _read_local_mionika_tokens()

    r = httpx.post(
        f"{cfg.server_url}/admin/users/{target}/mionika-tokens",
        json=payload,
        headers={"X-Admin-Token": cfg.admin_token},
        timeout=30,
    )
    if r.status_code >= 400:
        raise click.ClickException(f"server returned {r.status_code}: {r.text}")
    body = r.json()
    expires_at_ms = body.get("expires_at", 0)
    when = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc).isoformat()
    click.echo(f"Synced for user {target}. access_token expires at {when}.")
