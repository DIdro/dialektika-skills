import click

from ..config import load_config
from ..api_client import APIClient


@click.command(name="skip")
@click.argument("job_id")
def skip(job_id: str):
    """Mark a job as skipped (delete from inbox)."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    client.delete_job(job_id)
    click.echo(f"Skipped: {job_id}")
