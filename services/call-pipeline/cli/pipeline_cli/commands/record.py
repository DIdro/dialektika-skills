import re

import click

from ..config import load_config
from ..api_client import APIClient, APIError

# consumer Telemost + Yandex 360 (telemost.<sub>.yandex.ru)
URL_RE = re.compile(r"https?://telemost\.(?:[a-z0-9-]+\.)?yandex\.ru/\S+", re.I)


@click.command()
@click.argument("url")
@click.option("--num-speakers", type=int, default=None,
              help="Expected participant count — diarization hint (e.g. 2 for a 1:1).")
@click.option("--quick", is_flag=True,
              help="Also run a fast Groq pass — fetch it with `pipeline record-transcript <id>`.")
def record(url: str, num_speakers: int | None, quick: bool):
    """Record a Telemost call server-side. The diarized draft lands in the user's drafts.

    The recording runs on the pipeline host, auto-stops after ~5 min of silence
    (or `pipeline record-stop <id>`), and is submitted under your account — so
    the speaker-separated draft shows up via the user's drafts like a normal upload.
    """
    if not URL_RE.search(url):
        raise click.ClickException(
            "Provide a Telemost link (telemost.yandex.ru or telemost.360.yandex.ru).")
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    try:
        r = client.create_recording(url, num_speakers=num_speakers, quick=quick)
    except APIError as e:
        raise click.ClickException(str(e))
    click.echo(f"Recording: {r['recording_id']}  status: {r['status']}")
    click.echo("Recording on the host; auto-stops on silence (or "
               "`pipeline record-stop <id>`). The diarized draft will appear in "
               "the user's drafts.")
