"""REST-клиент общего call-pipeline. Только чтение job'ов владельца (под его токеном)."""
import httpx


def pick_short_jobs(jobs: list[dict]) -> list[dict]:
    return [j for j in jobs if j.get("mode") == "short"]


def list_by_status(base: str, token: str, status: str) -> list[dict]:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{base}/jobs", params={"status": status, "limit": 100},
                  headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json().get("jobs", [])


def list_processable(base: str, token: str) -> list[dict]:
    """Все job'ы с готовым транскриптом (любой режим), дедуп по job_id.
    transcribed — короткое окно; общий классификатор быстро двигает в drafted,
    поэтому слушаем оба статуса. Один звонок = один job (сервер дедупит по sha256)."""
    seen, out = set(), []
    for status in ("transcribed", "drafted"):
        for j in list_by_status(base, token, status):
            jid = j.get("job_id")
            if jid and jid not in seen:
                seen.add(jid)
                out.append(j)
    return out


def list_transcribed(base: str, token: str) -> list[dict]:
    return list_by_status(base, token, "transcribed")


def get_job(base: str, token: str, job_id: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{base}/jobs/{job_id}",
                  headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()


def delete_job(base: str, token: str, job_id: str) -> None:
    """skip: удалить job из инбокса call-pipeline (DELETE /jobs/{id}). Вызывать ТОЛЬКО после того,
    как ops-ingest сохранил свою копию транскрипта (иначе теряем источник для графа)."""
    with httpx.Client(timeout=30) as c:
        r = c.delete(f"{base}/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()


def render_transcript(payload: dict) -> str:
    """Транскрипт для классификатора — с разметкой спикеров, когда она есть.

    Диаризация (mode=call) — самый дорогой шаг пайплайна, и её результат лежит
    в `segments`. Плоское поле `text` склеено как " ".join(сегменты) и метки
    теряет, поэтому классификатор атрибутировал обещания наугад. Здесь реплики
    одного спикера подряд схлопываются в один блок: при медиане 405 сегментов
    на звонок метка на каждой реплике раздувает промпт, ничего не уточняя.

    Без диаризации (mode=short) метки не значат ничего — отдаём плоский текст.
    """
    flat = payload.get("text") or ""
    segments = payload.get("segments") or []
    if not payload.get("diarized") or not segments:
        return flat

    blocks: list[str] = []
    current: str | None = None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        if speaker != current or not blocks:
            blocks.append(f"[{speaker}] {text}" if speaker else text)
            current = speaker
        else:
            blocks[-1] += f" {text}"
    return "\n".join(blocks) or flat


def get_transcript_text(base: str, token: str, job_id: str) -> str:
    with httpx.Client(timeout=60) as c:
        r = c.get(f"{base}/jobs/{job_id}/transcript",
                  headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return render_transcript(r.json())


def transcript_facts(payload: dict) -> dict:
    """Факты диаризации для строки очереди.

    Голый флаг `diarized` не отвечает на вопрос «на чём стоит атрибуция»: пианота
    могла отработать, а половина сегментов остаться без метки. Считаем и это.
    """
    segments = payload.get("segments") or []
    if not payload.get("diarized") or not segments:
        return {"diarized": False, "speaker_count": 0, "unknown_share": 0.0}
    speakers = {s.get("speaker") for s in segments
                if s.get("speaker") and s.get("speaker") != "UNKNOWN"}
    unknown = sum(1 for s in segments if (s.get("speaker") or "UNKNOWN") == "UNKNOWN")
    return {"diarized": True, "speaker_count": len(speakers),
            "unknown_share": unknown / len(segments)}


def get_transcript_payload(base: str, token: str, job_id: str) -> dict:
    """Сырой payload транскрипта (текст — через существующий render_transcript)."""
    with httpx.Client(timeout=60) as c:
        r = c.get(f"{base}/jobs/{job_id}/transcript",
                  headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()
