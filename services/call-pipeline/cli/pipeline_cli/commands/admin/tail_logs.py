import json

import click
import httpx

from ...config import load_config


@click.command(name="tail-logs")
@click.argument("job_id")
def tail_logs(job_id: str):
    """Print all event-log entries for a job (admin only)."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")
    r = httpx.get(
        f"{cfg.server_url}/admin/jobs/{job_id}/events",
        headers={"X-Admin-Token": cfg.admin_token},
        timeout=30,
    )
    r.raise_for_status()
    click.echo(json.dumps(r.json(), indent=2, ensure_ascii=False))
