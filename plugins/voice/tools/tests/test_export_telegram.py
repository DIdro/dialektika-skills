import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_telegram import is_author_post, to_record


def test_forward_is_not_author_post():
    assert is_author_post({"fwd_from": {"channel_id": 1}, "text": "Длинный текст" * 30}) is False


def test_empty_text_is_not_author_post():
    assert is_author_post({"fwd_from": None, "text": "   "}) is False


def test_short_link_announce_is_not_author_post():
    msg = {"fwd_from": None, "text": "Записывайтесь https://example.com/event"}
    assert is_author_post(msg) is False


def test_long_text_with_link_is_author_post():
    msg = {"fwd_from": None, "text": "Мысль про работу. " * 20 + " https://example.com"}
    assert is_author_post(msg) is True


def test_plain_post_is_author_post():
    assert is_author_post({"fwd_from": None, "text": "Короткая, но своя мысль."}) is True


def test_to_record_shape():
    rec = to_record({"id": 42, "date": "2025-11-14T10:00:00+00:00", "text": "  текст  "})
    assert rec == {"id": 42, "date": "2025-11-14T10:00:00+00:00", "text": "текст"}


# --- параметризация окружения (пакет отчуждаемый: чужих ключей в нём нет) ---

ENV_BODY = '# комментарий\nTG_API_ID=12345\nTG_API_HASH="abc"\n\n'


def test_read_env_file_parses_keys(tmp_path):
    from export_telegram import read_env_file
    env = tmp_path / "telegram.env"
    env.write_text(ENV_BODY, encoding="utf-8")
    assert read_env_file(env) == {"TG_API_ID": "12345", "TG_API_HASH": "abc"}


def test_read_env_file_missing_is_empty(tmp_path):
    from export_telegram import read_env_file
    assert read_env_file(tmp_path / "нет.env") == {}


def test_credentials_come_from_args_then_env_file_then_environ(tmp_path, monkeypatch):
    """Порядок: аргументы → .env (из --env или конфига) → переменные окружения."""
    from argparse import Namespace
    from config import load_config
    from export_telegram import resolve_credentials

    (tmp_path / "telegram.env").write_text(
        "TG_API_ID=222\nTG_API_HASH=hhh\n", encoding="utf-8")
    (tmp_path / "voice.config.yaml").write_text(
        "profile: ./profile\ntelegram:\n  env: ./telegram.env\n", encoding="utf-8")
    cfg = load_config(tmp_path / "voice.config.yaml")

    monkeypatch.setenv("TG_API_ID", "333")
    monkeypatch.setenv("TG_API_HASH", "eee")

    assert resolve_credentials(
        Namespace(api_id="111", api_hash="aaa", env=None), cfg) == ("111", "aaa")
    assert resolve_credentials(
        Namespace(api_id=None, api_hash=None, env=None), cfg) == ("222", "hhh")
    assert resolve_credentials(
        Namespace(api_id=None, api_hash=None, env=None), None) == ("333", "eee")


def test_session_path_comes_from_config_or_argument(tmp_path):
    from argparse import Namespace
    from config import load_config
    from export_telegram import resolve_session

    (tmp_path / "voice.config.yaml").write_text(
        "profile: ./profile\ntelegram:\n  session: ./сессия/tg\n", encoding="utf-8")
    cfg = load_config(tmp_path / "voice.config.yaml")

    assert resolve_session(Namespace(session=None), cfg) == (
        tmp_path / "сессия" / "tg").resolve()
    assert resolve_session(Namespace(session="/явный/путь"), cfg) == Path("/явный/путь")


def test_session_defaults_next_to_config(tmp_path):
    from argparse import Namespace
    from config import load_config
    from export_telegram import resolve_session

    (tmp_path / "voice.config.yaml").write_text("profile: ./profile\n", encoding="utf-8")
    cfg = load_config(tmp_path / "voice.config.yaml")
    assert resolve_session(Namespace(session=None), cfg) == cfg.root / "tg"


def test_no_session_or_env_shipped_in_the_package():
    """В пакете не должно быть ни ключей, ни живых сессий."""
    kit = Path(__file__).resolve().parents[2]
    bad = sorted(str(p) for p in kit.rglob("*")
                 if p.is_file() and (p.suffix in (".session", ".env")
                                     or p.name in (".env", "tg.session")))
    assert bad == [], f"в пакете лежат секреты: {bad}"
