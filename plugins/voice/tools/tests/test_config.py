# -*- coding: utf-8 -*-
"""Тесты загрузчика конфига.

Конфиг — единственное место, где отчуждаемый пакет узнаёт про конкретного
человека. Ошибка в нём должна быть внятным сообщением, а не traceback, а
статус источника — честным: «сконфигурирован» ≠ «путь существует».
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from config import (
    CONFIGURED,
    MISSING_PATH,
    NOT_CONFIGURED,
    SOURCE_TYPES,
    Config,
    ConfigError,
    find_config,
    load_config,
    parse_yaml,
)

MINIMAL = "author: Кто-то\nprofile: ./profile\n"


def write(tmp_path: Path, body: str, name: str = "voice.config.yaml") -> Path:
    (tmp_path / "profile" / "corpus").mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# --- разбор ---

def test_parses_full_example_schema(tmp_path):
    cfg = load_config(write(tmp_path, """
author: Имя Фамилия
profile: ./profile
sources:
  - type: mionika
    projects: [альфа, бета]
  - type: fact-registry
    path: ./facts.md
    precedence: canon
  - type: files
    glob: "notes/**/*.md"
  - type: corpus
  - type: claude-sessions
    path: "~/.claude/projects/x/*.jsonl"
    optional: true
python: C:/python/python.exe
"""))
    assert cfg.author == "Имя Фамилия"
    assert [s.type for s in cfg.sources] == [
        "mionika", "fact-registry", "files", "corpus", "claude-sessions"]
    assert cfg.sources[0].projects == ["альфа", "бета"]
    assert cfg.sources[1].precedence == "canon"
    assert cfg.sources[2].glob == "notes/**/*.md"
    assert cfg.sources[4].optional is True


def test_mini_yaml_used_when_pyyaml_absent(monkeypatch):
    """Пакет не обязан тащить PyYAML: встроенный разборщик даёт то же дерево."""
    import builtins
    real_import = builtins.__import__

    def no_yaml(name, *a, **kw):
        if name == "yaml":
            raise ImportError("нет PyYAML")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    data = parse_yaml("author: Кто-то\nsources:\n  - type: mionika\n"
                      "    projects: [а, б]\n  - type: corpus\n")
    assert data == {"author": "Кто-то",
                    "sources": [{"type": "mionika", "projects": ["а", "б"]},
                                {"type": "corpus"}]}


def test_mini_yaml_agrees_with_pyyaml_on_the_example_config():
    """Два разборщика обязаны давать одно дерево — иначе конфиг ведёт себя
    по-разному в зависимости от того, установлен PyYAML или нет."""
    yaml = pytest.importorskip("yaml", reason="PyYAML не установлен")
    from config import _mini_yaml
    example = (Path(__file__).resolve().parents[2] / "voice.config.example.yaml")
    text = example.read_text(encoding="utf-8")
    assert _mini_yaml(text) == yaml.safe_load(text)


def test_example_config_is_valid(tmp_path):
    """Пример из коробки должен загружаться без ошибок валидации."""
    example = (Path(__file__).resolve().parents[2] / "voice.config.example.yaml")
    dst = tmp_path / "voice.config.yaml"
    dst.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(dst)
    assert cfg.author and cfg.profile.name == "profile"
    assert sum(1 for s in cfg.sources if s.precedence == "canon") == 1


def test_comments_and_quotes_survive_mini_yaml():
    data = parse_yaml('author: "Имя # не комментарий"  # а это комментарий\n'
                      "profile: ./profile\n")
    assert data == {"author": "Имя # не комментарий", "profile": "./profile"}


def test_json_config_has_the_same_schema(tmp_path):
    body = json.dumps({"author": "Кто-то", "profile": "./profile",
                       "sources": [{"type": "corpus"}]}, ensure_ascii=False)
    cfg = load_config(write(tmp_path, body, name="voice.config.json"))
    assert cfg.author == "Кто-то"
    assert [s.type for s in cfg.sources] == ["corpus"]


# --- валидация ---

def test_two_canon_sources_is_a_clear_error(tmp_path):
    path = write(tmp_path, """
profile: ./profile
sources:
  - type: fact-registry
    path: ./facts.md
    precedence: canon
  - type: files
    glob: "notes/*.md"
    precedence: canon
""")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    msg = str(e.value)
    assert "canon" in msg
    assert "fact-registry" in msg and "files" in msg
    assert "ровно один" in msg


def test_single_canon_source_is_fine(tmp_path):
    cfg = load_config(write(tmp_path, """
profile: ./profile
sources:
  - type: corpus
    precedence: canon
  - type: files
    glob: "notes/*.md"
"""))
    assert [s.precedence for s in cfg.sources] == ["canon", None]


def test_unknown_source_type_lists_supported(tmp_path):
    path = write(tmp_path, "profile: ./profile\nsources:\n  - type: телепатия\n")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    msg = str(e.value)
    assert "телепатия" in msg
    for t in SOURCE_TYPES:
        assert t in msg


def test_source_without_type_is_an_error(tmp_path):
    path = write(tmp_path, "profile: ./profile\nsources:\n  - glob: \"*.md\"\n")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "type" in str(e.value)


def test_unknown_precedence_value_is_an_error(tmp_path):
    path = write(tmp_path, """
profile: ./profile
sources:
  - type: corpus
    precedence: главный
""")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "precedence" in str(e.value) and "canon" in str(e.value)


def test_missing_profile_is_an_error(tmp_path):
    path = write(tmp_path, "author: Кто-то\n")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "profile" in str(e.value)


def test_missing_config_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "нет-такого.yaml")
    assert "нет файла конфига" in str(e.value)


def test_sources_must_be_a_list(tmp_path):
    path = write(tmp_path, "profile: ./profile\nsources: corpus\n")
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "списком" in str(e.value)


def test_errors_are_config_error_not_traceback(tmp_path):
    """Все ошибки конфига — один тип, который CLI печатает сообщением."""
    for body in ("author: Кто-то\n",
                 "profile: ./profile\nsources:\n  - type: неведомое\n",
                 "profile: ./profile\nsources: 42\n"):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, body))


# --- пути ---

def test_relative_paths_resolve_against_config_dir(tmp_path):
    nested = tmp_path / "кит"
    nested.mkdir()
    cfg = load_config(write(nested, "profile: ./profile\npython: ./bin/python.exe\n"))
    assert cfg.profile == (nested / "profile").resolve()
    assert cfg.python == (nested / "bin" / "python.exe").resolve()
    assert cfg.corpus_dir == (nested / "profile" / "corpus").resolve()
    assert cfg.stats_path == (nested / "profile" / "stats.json").resolve()


def test_tilde_is_expanded(tmp_path):
    cfg = load_config(write(tmp_path, """
profile: ~/голос-профиль
sources:
  - type: claude-sessions
    path: "~/.claude/projects/x/*.jsonl"
"""))
    home = Path.home()
    assert cfg.profile == home / "голос-профиль"
    assert str(cfg.sources[0].path).startswith(str(home))
    assert "~" not in str(cfg.profile)


def test_absolute_paths_are_kept(tmp_path):
    other = (tmp_path / "другое-место").resolve()
    cfg = load_config(write(tmp_path, f"profile: {other.as_posix()}\n"))
    assert cfg.profile == other


def test_find_config_walks_up_the_tree(tmp_path, monkeypatch):
    write(tmp_path, MINIMAL)
    deep = tmp_path / "а" / "б" / "в"
    deep.mkdir(parents=True)
    assert find_config(deep) == tmp_path / "voice.config.yaml"


def test_load_config_without_path_finds_it(tmp_path, monkeypatch):
    write(tmp_path, MINIMAL)
    monkeypatch.delenv("VOICE_KIT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load_config().path == (tmp_path / "voice.config.yaml").resolve()


def test_env_var_points_at_config(tmp_path, monkeypatch):
    path = write(tmp_path, MINIMAL)
    monkeypatch.setenv("VOICE_KIT_CONFIG", str(path))
    assert load_config().author == "Кто-то"


# --- статус источников ---

def test_status_marks_missing_path_separately_from_not_configured(tmp_path):
    cfg = load_config(write(tmp_path, """
profile: ./profile
sources:
  - type: fact-registry
    path: ./нет-такого.md
  - type: mionika
  - type: corpus
"""))
    by_type = {s["type"]: s for s in cfg.source_status()}
    assert by_type["fact-registry"]["status"] == MISSING_PATH
    assert by_type["mionika"]["status"] == NOT_CONFIGURED
    # profile/corpus создан фикстурой, но пустой — это тоже «пути нет»
    assert by_type["corpus"]["status"] == MISSING_PATH


def test_status_is_configured_when_everything_is_in_place(tmp_path):
    (tmp_path / "facts.md").write_text("факт", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "one.md").write_text("заметка", encoding="utf-8")
    cfg = load_config(write(tmp_path, """
profile: ./profile
sources:
  - type: fact-registry
    path: ./facts.md
    precedence: canon
  - type: files
    glob: "notes/**/*.md"
  - type: mionika
    projects: [альфа]
  - type: corpus
"""))
    (tmp_path / "profile" / "corpus" / "2026-01-01-1.md").write_text(
        "пост", encoding="utf-8")
    by_type = {s["type"]: s for s in cfg.source_status()}
    assert by_type["fact-registry"]["status"] == CONFIGURED
    assert by_type["files"]["status"] == CONFIGURED
    assert by_type["mionika"]["status"] == CONFIGURED
    assert by_type["corpus"]["status"] == CONFIGURED
    assert by_type["fact-registry"]["precedence"] == "canon"


def test_status_keeps_optional_flag(tmp_path):
    cfg = load_config(write(tmp_path, """
profile: ./profile
sources:
  - type: claude-sessions
    path: "./сессии/*.jsonl"
    optional: true
"""))
    st = cfg.source_status()[0]
    assert st["optional"] is True
    assert st["status"] == MISSING_PATH


def test_status_has_a_human_label_for_every_source(tmp_path):
    cfg = load_config(write(tmp_path, """
profile: ./profile
sources:
  - type: corpus
  - type: mionika
"""))
    for st in cfg.source_status():
        assert st["status_label"]
        assert st["detail"]


def test_format_status_mentions_author_profile_and_sources(tmp_path):
    cfg = load_config(write(tmp_path, """
author: Имя Фамилия
profile: ./profile
sources:
  - type: corpus
"""))
    out = cfg.format_status()
    assert "Имя Фамилия" in out
    assert "profile" in out
    assert "corpus" in out


def test_profile_status_lists_expected_artifacts(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    names = {p["name"] for p in cfg.profile_status()}
    assert {"corpus", "articles", "edits", "stats.json",
            "style-card.md", "banned.md"} <= names


def test_unknown_top_level_keys_are_reported_not_fatal(tmp_path):
    cfg = load_config(write(tmp_path, "profile: ./profile\nопечатка: 1\n"))
    assert cfg.unknown_keys == ["опечатка"]
    assert "опечатка" in cfg.format_status()


def test_telegram_block_is_optional_and_resolved(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.telegram_env is None and cfg.telegram_session is None
    cfg2 = load_config(write(tmp_path, """
profile: ./profile
telegram:
  env: ./telegram.env
  session: ./tg
"""))
    assert cfg2.telegram_env == (tmp_path / "telegram.env").resolve()
    assert cfg2.telegram_session == (tmp_path / "tg").resolve()


def test_config_object_can_be_built_from_a_dict(tmp_path):
    cfg = Config({"profile": "./profile"}, tmp_path / "voice.config.yaml")
    assert cfg.profile == (tmp_path / "profile").resolve()
    assert cfg.author is None
