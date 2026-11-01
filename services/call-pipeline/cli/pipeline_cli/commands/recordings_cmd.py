import click
from rich.console import Console
from rich.table import Table

from ..config import load_config
from ..api_client import APIClient


@click.command(name="recordings")
@click.option("--status", default=None,
              help="Filter by status (requested/recording/recorded/submitted/failed).")
def recordings(status: str | None):
    """List your Telemost recordings."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    rows = client.list_recordings(status=status)
    t = Table("ID", "Status", "Speakers", "Job", "Created")
    t.columns[0].no_wrap = True
    for r in rows:
        t.add_row(
            r["recording_id"], r["status"],
            str(r.get("num_speakers") or "-"),
            (r.get("job_id") or "-"), r["created_at"],
        )
    Console().print(t)
