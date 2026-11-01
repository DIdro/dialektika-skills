import click

from ..config import load_config
from ..api_client import APIClient, APIError


@click.command(name="record-stop")
@click.argument("recording_id")
def record_stop(recording_id: str):
    """Stop an in-progress Telemost recording."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    try:
        r = client.stop_recording(recording_id)
    except APIError as e:
        raise click.ClickException(str(e))
    click.echo(f"{r['recording_id']}: stop_requested={r.get('stop_requested')} "
               f"(status {r['status']})")
