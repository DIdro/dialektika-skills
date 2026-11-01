import click
import httpx

from ...config import load_config


@click.command(name="rotate-token")
@click.argument("user_id")
def rotate_token(user_id: str):
    """Rotate a user's bearer token (admin only)."""
    cfg = load_config()
    if not cfg.admin_token:
        raise click.ClickException("admin_token missing in config")
    r = httpx.post(
        f"{cfg.server_url}/admin/users/{user_id}/rotate-token",
        headers={"X-Admin-Token": cfg.admin_token},
        timeout=30,
    )
    r.raise_for_status()
    click.echo(f"new token: {r.json()['bearer_token']}")
