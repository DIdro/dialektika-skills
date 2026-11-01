import json

import click
import httpx

from ...config import load_config


@click.command(name="add-user")
@click.option("--name", required=True)
@click.option("--mionika-token", default=None, help="Mionika read-token (encrypted at rest).")
@click.option("--projects", default="", help="Comma-separated project keys for classifier.")
@click.option("--context", default=None, help="200-word user bio for classifier system prompt.")
def add_user(name: str, mionika_token: str | None, projects: str, context: str | None):
    """Create a user (admin only)."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")
    body = {
        "name": name,
        "mionika_active_project_keys": [k.strip() for k in projects.split(",") if k.strip()],
    }
    if mionika_token:
        body["mionika_read_token"] = mionika_token
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
