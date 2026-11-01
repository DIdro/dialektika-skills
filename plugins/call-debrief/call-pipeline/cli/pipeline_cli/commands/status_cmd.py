import click
from rich.console import Console

from ..config import load_config
from ..api_client import APIClient


@click.command(name="status")
def status():
    """Quick summary: pending count + disk warning."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    health = client.health()
    pending = (
        len(client.list_jobs(status="drafted"))
        + len(client.list_jobs(status="transcribed"))
        + len(client.list_jobs(status="uploaded"))
    )
    c = Console()
    if health.get("disk_warning"):
        free = health.get("disk_free_gb")
        free_str = f"{free:.1f} GB" if free is not None else "unknown"
        c.print(f"[bold red]⚠ disk warning: free {free_str}[/bold red]")
    c.print(f"Pending jobs: {pending}")
