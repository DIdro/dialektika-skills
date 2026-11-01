"""ASGI-вход для uvicorn: конфиг из окружения, та же SQLite, что у демона."""
from .api import create_app
from .run import config_from_env
from .store import Store

_cfg = config_from_env()
app = create_app(_cfg, Store(_cfg.db_path))
