import json

import click

from ...config import load_config
from ...api_client import APIClient


@click.command(name="disk-status")
def disk_status():
    """Show VPS disk usage snapshot (admin only)."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")
    client = APIClient(
        base_url=cfg.server_url,
        token=cfg.bearer_token,
        admin_token=cfg.admin_token,
    )
    click.echo(json.dumps(client.admin_disk_status(), indent=2))
