# -*- coding: utf-8 -*-
"""Выгрузка авторских постов Telegram-канала до отсечки по дате.

    python export_telegram.py --channel мойканал --before 2026-01-01 --count-only
    python export_telegram.py --channel мойканал --before 2026-01-01 --out out/raw.jsonl

ЭТО НЕОБЯЗАТЕЛЬНЫЙ ПУТЬ. Корпус можно собрать и без Telegram: положи свои
тексты в `profile/corpus/` руками (по файлу на пост, с фронтматтером
`date`/`id`/`genre` — как их пишет build_corpus.py) и сразу запускай
stats.py. Telegram нужен только затем, чтобы не переносить сотню постов
вручную.

Что нужно человеку, который поедет этим путём — СВОИ ключи, чужие не
подойдут и в пакет не кладутся:

  1. Получить `TG_API_ID` и `TG_API_HASH` на https://my.telegram.org →
     API development tools.
  2. Отдать их скрипту одним из трёх способов (проверяются в этом порядке):
       --api-id / --api-hash        аргументами;
       --env путь/к/файлу.env       файл со строками TG_API_ID=… / TG_API_HASH=…
                                    (или `telegram.env` в voice.config.yaml);
       переменные окружения         TG_API_ID / TG_API_HASH.
  3. Указать, где держать файл сессии Telethon: --session путь/к/tg
     (или `telegram.session` в voice.config.yaml; по умолчанию — `tg` рядом
     с конфигом). Первый запуск спросит телефон и код подтверждения и создаст
     `<session>.session`.

Ни ключей, ни файла сессии в пакете нет и быть не должно: `.session` — это
живой доступ к аккаунту. `.gitignore` пакета их отсекает.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from config import ConfigError, add_config_argument, load_config

HERE = Path(__file__).parent

MIN_LEN_FOR_LINK_ONLY = 200


def is_author_post(msg: dict) -> bool:
    """Авторский текст: не репост, не пусто, не короткий анонс со ссылкой."""
    if msg.get("fwd_from"):
        return False
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    if len(text) < MIN_LEN_FOR_LINK_ONLY and "http" in text:
        return False
    return True


def to_record(msg: dict) -> dict:
    return {
        "id": msg["id"],
        "date": msg["date"],
        "text": (msg.get("text") or "").strip(),
    }


def _msg_to_dict(m) -> dict:
    return {
        "id": m.id,
        "date": m.date.astimezone(timezone.utc).isoformat(),
        "text": m.message or "",
        "fwd_from": m.fwd_from,
    }


def read_env_file(path: Path) -> dict:
    """Читает KEY=VALUE из .env. Без python-dotenv — лишняя зависимость."""
    out = {}
    if not path or not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("\"'")
    return out


def resolve_credentials(args, cfg):
    """Ключи: аргументы → .env (аргумент или конфиг) → переменные окружения."""
    api_id, api_hash = args.api_id, args.api_hash
    if not (api_id and api_hash):
        env_path = Path(args.env) if args.env else (cfg.telegram_env if cfg else None)
        env = read_env_file(env_path) if env_path else {}
        api_id = api_id or env.get("TG_API_ID")
        api_hash = api_hash or env.get("TG_API_HASH")
    api_id = api_id or os.environ.get("TG_API_ID")
    api_hash = api_hash or os.environ.get("TG_API_HASH")
    return api_id, api_hash


def resolve_session(args, cfg) -> Path:
    if args.session:
        return Path(args.session).expanduser()
    if cfg and cfg.telegram_session:
        return cfg.telegram_session
    if cfg:
        return cfg.root / "tg"
    return HERE / "tg"


def main():
    ap = argparse.ArgumentParser(description="Выгрузка авторских постов канала")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--before", required=True, help="YYYY-MM-DD, строго раньше этой даты")
    ap.add_argument("--out", default="out/raw.jsonl")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--env", default=None,
                    help="файл с TG_API_ID/TG_API_HASH (по умолчанию — telegram.env из конфига)")
    ap.add_argument("--session", default=None,
                    help="путь к файлу сессии Telethon без расширения "
                         "(по умолчанию — telegram.session из конфига)")
    ap.add_argument("--api-id", default=None)
    ap.add_argument("--api-hash", default=None)
    add_config_argument(ap)
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError:
        # Конфиг для выгрузки не обязателен: ключи и сессию можно задать
        # аргументами или переменными окружения.
        cfg = None

    from telethon.sync import TelegramClient

    cutoff = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    api_id, api_hash = resolve_credentials(args, cfg)
    if not api_id or not api_hash:
        raise SystemExit(
            "Нет TG_API_ID/TG_API_HASH. Возьми свои на https://my.telegram.org и "
            "передай через --api-id/--api-hash, через --env файл.env, через "
            "`telegram.env` в voice.config.yaml или переменными окружения. "
            "Чужие ключи в пакет не кладутся.")

    session = resolve_session(args, cfg)
    session.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = []
    with TelegramClient(str(session), int(api_id), api_hash) as client:
        if not client.is_user_authorized():
            raise SystemExit(
                f"Сессия {session}.session не авторизована — запусти этот же "
                f"скрипт и введи телефон и код подтверждения.")
        entity = client.get_entity(args.channel)
        for m in client.iter_messages(entity, offset_date=cutoff):
            total += 1
            d = _msg_to_dict(m)
            if is_author_post(d):
                kept.append(to_record(d))

    print(f"всего сообщений до {args.before}: {total}")
    print(f"из них авторских постов: {len(kept)}")

    if args.count_only:
        return

    out_path = (Path(args.out) if Path(args.out).is_absolute()
                else HERE / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"записано: {out_path}")


if __name__ == "__main__":
    main()
