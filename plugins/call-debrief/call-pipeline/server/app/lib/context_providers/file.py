"""Список проектов из файла: одна строка — `ключ — название — о чём`.

Самый простой источник контекста. Свою базу (трекер, вики, граф) подключают
отдельным классом по протоколу из base.py.
"""
import os
from pathlib import Path


class FileProvider:
    name = "file"

    def _path(self) -> Path | None:
        raw = os.environ.get("PIPELINE_PROJECTS_FILE")
        return Path(raw) if raw else None

    def is_available(self, user_config: dict) -> bool:
        return self._path() is not None

    async def fetch_context(self, transcript: str, user_config: dict) -> dict:
        path = self._path()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            return {"projects": [], "error": str(e)}
        projects = []
        for line in lines:
            parts = [p.strip() for p in line.split("—")]
            if len(parts) >= 2 and parts[0] and " " not in parts[0]:
                projects.append({
                    "key": parts[0],
                    "name": parts[1],
                    "about": " — ".join(parts[2:]),
                })
        return {"projects": projects}
