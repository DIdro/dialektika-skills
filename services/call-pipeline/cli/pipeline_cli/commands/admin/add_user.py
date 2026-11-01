import json

import click
import httpx

from ...config import load_config


@click.command(name="add-user")
@click.option("--name", required=True)
@click.option("--context", default=None, help="200-word user bio for classifier system prompt.")
def add_user(name: str, context: str | None):
    """Create a user (admin only)."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")
    body = {"name": name}
    if context:
        body["context_summary"] = context
    r = httpx.post(
        f"{cfg.server_url}/admin/users",
        json=body,
        headers={"X-Admin-Token": cfg.admin_token},
        timeout=30,
    )
    r.raise_for_status()
    click.echo(json.dumps(r.json(), indent=2))
