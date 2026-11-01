import json

import click

from ..config import load_config
from ..api_client import APIClient


def _fmt_ts(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _format_speakers(payload: dict) -> str:
    """Group transcript segments into per-speaker turns.

    Falls back to the flat `text` when the transcript carries no speaker
    labels (e.g. a `short`-mode job that wasn't diarized).
    """
    segs = payload.get("segments") or []
    if not any(s.get("speaker") for s in segs):
        return payload.get("text", "")

    lines: list[str] = []
    cur: str | None = None
    buf: list[str] = []
    t0: float = 0.0
    for s in segs:
        spk = s.get("speaker") or "UNKNOWN"
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        if spk != cur:
            if buf:
                lines.append(f"[{_fmt_ts(t0)}] {cur}: {' '.join(buf)}")
            cur, buf, t0 = spk, [txt], s.get("start", 0) or 0
        else:
            buf.append(txt)
    if buf:
        lines.append(f"[{_fmt_ts(t0)}] {cur}: {' '.join(buf)}")
    return "\n".join(lines)


@click.command(name="show")
@click.argument("job_id")
@click.option("--transcript", is_flag=True, help="Print the transcript text (flat).")
@click.option("--speakers", is_flag=True,
              help="Print the transcript grouped by speaker with timecodes "
                   "(uses diarization; falls back to flat text if undiarized).")
@click.option("--draft", is_flag=True, help="Print the classifier draft JSON.")
def show(job_id: str, transcript: bool, speakers: bool, draft: bool):
    """Show details for a job."""
    cfg = load_config()
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    if draft:
        click.echo(json.dumps(client.get_draft(job_id), indent=2, ensure_ascii=False))
    elif speakers:
        click.echo(_format_speakers(client.get_transcript(job_id)))
    elif transcript:
        click.echo(client.get_transcript(job_id).get("text", ""))
    else:
        click.echo(json.dumps(client.get_job(job_id), indent=2, ensure_ascii=False))
