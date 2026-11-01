"""Режим без Мионики: проекты из файла, принятый звонок — в .md и .json.

Включается переменными OPS_INGEST_PROJECTS_FILE и OPS_INGEST_ACCEPTED_DIR.
Раскладки по узлам и проверки на противоречия здесь нет: для них нужно
содержимое графа.
"""
import json
from pathlib import Path

from . import mionika_client as mio


async def fetch_projects(cfg) -> list[dict]:
    path = getattr(cfg, "projects_file", "")
    if not path:
        return await mio.fetch_projects(cfg.mionika_token)
    projects = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("—")]
        if len(parts) >= 2 and parts[0] and " " not in parts[0]:
            projects.append({"key": parts[0], "name": parts[1]})
    return projects


def write_accepted(accepted_dir: str, job_id: str, project: str, row: dict) -> int:
    """Возвращает число записанных пунктов."""
    atoms, tasks = row.get("atoms") or [], row.get("tasks") or []
    out = Path(accepted_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{project}-{job_id}"
    (out / f"{stem}.json").write_text(json.dumps(
        {"job_id": job_id, "project": project, "summary": row.get("summary"),
         "recorded_at": row.get("recorded_at"), "atoms": atoms, "tasks": tasks},
        ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {project}: {job_id}", "", row.get("summary") or "", "", "## Знания", ""]
    lines += [f"- {a.get('kind') or a.get('type') or 'note'}: {a.get('text') or a.get('title') or ''}"
              for a in atoms]
    lines += ["", "## Задачи", ""]
    lines += [f"- {t.get('title') or ''} ({t.get('owner') or ''})" for t in tasks]
    (out / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(atoms) + len(tasks)
