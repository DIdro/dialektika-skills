# -*- coding: utf-8 -*-
"""Общие фикстуры тестов voice-kit.

Пакет отчуждаемый: тесты обязаны проходить на голом клоне — без корпуса, без
профиля и без чьих-либо личных данных. Поэтому пороги для регрессионных
фикстур берутся из `fixtures/stats.reference.json` (эталон под фикстуры, а не
чей-то профиль), а тесты, которым нужен настоящий профиль, работают с ним
только если он сконфигурирован, и в остальных случаях строят синтетический.
"""

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

FIXTURES = TOOLS / "fixtures"
REFERENCE_STATS = FIXTURES / "stats.reference.json"


@pytest.fixture(scope="session")
def tools_dir() -> Path:
    return TOOLS


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def stats() -> dict:
    """Пороги для регрессионных фикстур — из эталонного файла пакета.

    Раньше этот тест читал боевой stats.json владельца голоса, чтобы падать
    при сдвиге порогов в проде. В отчуждаемом пакете такого файла нет и быть
    не должно, поэтому ожидания фикстур закреплены за эталоном: он меняется
    только вместе с самими фикстурами. Дрейф РЕАЛЬНЫХ порогов ловит
    test_stats.py::test_real_corpus_thresholds_hit_false_positive_target.
    """
    return json.loads(REFERENCE_STATS.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def profile_config():
    """Config, если рядом лежит настоящий конфиг с существующим профилем.

    Иначе None — тесты, которым нужен профиль, в этом случае работают на
    синтетике или пропускаются. На голом клоне пакета это норма.
    """
    from config import ConfigError, load_config
    try:
        cfg = load_config()
    except ConfigError:
        return None
    return cfg if cfg.profile.exists() else None


def write_config(tmp_path: Path, body: str, name: str = "voice.config.yaml") -> Path:
    """Пишет конфиг во временный каталог и возвращает путь к нему."""
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p
