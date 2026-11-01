"""Persistent per-user whitelist for the Telemost control bot.

Stores each allowed user's id plus best-effort metadata (display name and
@username) so /list can show who's who, without surfacing numeric ids. JSON on
disk, edited at runtime via bot commands. Single asyncio writer, await-free
mutations — atomic within the event loop, no lock needed.
"""
import json
import os
from pathlib import Path
from collections.abc import Iterable

DEFAULT_PATH = "/var/lib/call-pipeline/telemost_whitelist.json"


class Whitelist:
    def __init__(self, seed_ids: Iterable[int] = ()):
        self._path = Path(os.environ.get("TELEMOST_WHITELIST_FILE", DEFAULT_PATH))
        self._users: dict[int, dict] = {}
        if self._path.exists():
            self._load()
        else:
            self._users = {int(x): {} for x in seed_ids}
            self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._users = {int(k): (v or {}) for k, v in (data.get("users") or {}).items()}
        except Exception:
            self._users = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": {str(k): v for k, v in sorted(self._users.items())}}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def __contains__(self, uid: int) -> bool:
        return int(uid) in self._users

    def add(self, uid: int, name: str | None = None, username: str | None = None) -> None:
        uid = int(uid)
        rec = self._users.get(uid, {})
        if name:
            rec["name"] = name
        if username:
            rec["username"] = username
        self._users[uid] = rec
        self._save()

    def remove(self, uid: int) -> None:
        self._users.pop(int(uid), None)
        self._save()

    def update_meta(self, uid: int, name: str | None = None, username: str | None = None) -> bool:
        uid = int(uid)
        if uid not in self._users:
            return False
        rec = self._users[uid]
        changed = False
        if name and rec.get("name") != name:
            rec["name"] = name; changed = True
        if username and rec.get("username") != username:
            rec["username"] = username; changed = True
        if changed:
            self._save()
        return changed

    def meta(self, uid: int) -> dict:
        return self._users.get(int(uid), {})

    def all(self) -> list[int]:
        return sorted(self._users)
