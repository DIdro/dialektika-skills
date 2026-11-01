import click

from ..config import load_config
from ..api_client import APIClient, APIError


@click.command(name="record-transcript")
@click.argument("recording_id")
def record_transcript(recording_id: str):
    """Print the fast Groq transcript of a `--quick` recording (no speakers).

    The speaker-diarized version always arrives separately in the user's drafts;
    this is the quick read available a minute or so after the call ends.
    """
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    try:
        r = client.get_recording_quick(recording_id)
    except LookupError:
        raise click.ClickException("recording not found")
    except APIError as e:
        raise click.ClickException(str(e))
    if not r.get("ready"):
        raise click.ClickException(
            f"Quick transcript not ready yet (recording status: {r.get('status')}).")
    click.echo(r["quick_transcript"])
