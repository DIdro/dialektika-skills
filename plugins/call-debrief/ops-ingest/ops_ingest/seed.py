"""Засеять курсор разобранного текущим бэклогом — чтобы первый запуск сервиса
разбирал ТОЛЬКО новые звонки, а не вывалил пачку уведомлений по всем старым.

Запуск (с теми же env, что и сервис):
    python -m ops_ingest.seed
"""
from . import pipeline_client as cp
from .run import config_from_env
from .store import Store


def main():
    cfg = config_from_env()
    store = Store(cfg.db_path)
    jobs = cp.list_processable(cfg.cp_base, cfg.cp_token)
    n = 0
    for j in jobs:
        jid = j.get("job_id")
        if jid and not store.is_processed(jid):
            store.mark_processed(jid, "")   # бэклог: помечаем разобранным, не трогаем
            n += 1
    print(f"seeded {n} existing jobs as processed; service will only handle NEW calls")


if __name__ == "__main__":
    main()
