import hashlib
from pathlib import Path

import click

from ..config import load_config
from ..api_client import APIClient, APIError
from ..audio_extract import is_video_container, extract_audio
from .watch import origin_for, recorded_date_of


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--mode", type=click.Choice(["call", "short"]), default="call",
              help="Whisper queue: call (long, with diarize) or short (no diarization).")
@click.option("--num-speakers", type=int, default=None,
              help="Expected number of speakers. Reduces pyannote over-segmentation "
                   "(e.g. --num-speakers 2 for a one-on-one call).")
@click.option("--keep-extracted/--delete-extracted", default=False,
              help="Keep the .opus produced from a video, instead of deleting after upload.")
@click.option("--origin", default=None,
              help="Происхождение записи: zoom / downloads / telegram-desktop / web / telemost. "
                   "По умолчанию определяется по папке файла, как это делает watch. "
                   "Признак нужен гейту разбора: без него запись считается присланным "
                   "чужим материалом и помечается стопом.")
def upload(path: Path, mode: str, num_speakers: int | None, keep_extracted: bool,
           origin: str | None):
    """Upload an audio/video file. Video is audio-extracted first."""
    cfg = load_config()
    target = path
    extracted = None
    if is_video_container(path):
        click.echo(f"Extracting audio from {path.name}…")
        extracted = extract_audio(path)
        target = extracted
        click.echo(f"  → {extracted.name} ({extracted.stat().st_size // 1024} KB)")
    # Происхождение считаем по ИСХОДНОМУ пути, а не по извлечённому аудио:
    # у временного файла папка уже другая, и признак потерялся бы.
    origin = origin or origin_for(path)
    dedupe = _sha256(target)
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    try:
        result = client.upload(
            target, mode=mode, user_id=cfg.user_id,
            dedupe_key=dedupe, num_speakers=num_speakers, origin=origin,
            # запасная дата — по исходному пути: у извлечённого аудио mtime «сегодня»
            fallback_recorded_at=recorded_date_of(path),
        )
    except APIError as e:
        click.echo(f"Upload failed: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"Job: {result['job_id']}  status: {result['status']}  origin: {origin}")
    if extracted and not keep_extracted:
        extracted.unlink()
