import click
from rich.console import Console
from rich.table import Table

from ..config import load_config
from ..api_client import APIClient


@click.command(name="list")
@click.option("--status", default=None, help="Filter by status (uploaded/transcribed/drafted/done/skipped/failed_*).")
def list_(status: str | None):
    """List jobs."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    rows = client.list_jobs(status=status)
    # Full job_id (not truncated): `show`/`delete`/API all need the complete
    # UUID, so the table must print something directly copy-pasteable.
    t = Table("ID", "Status", "Mode", "Created")
    t.columns[0].no_wrap = True
    for j in rows:
        t.add_row(j["job_id"], j["status"], j["mode"], j["created_at"])
    Console().print(t)
