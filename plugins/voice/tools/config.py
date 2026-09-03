# -*- coding: utf-8 -*-
"""Загрузка и валидация конфига voice-kit.

    python config.py                  # найти конфиг и напечатать статус источников
    python config.py --config путь/к/voice.config.yaml

Конфиг — единственное место, где инструменты узнают про конкретного человека:
кто автор, где лежит его профиль (корпус, карты, статистики, база правок),
из каких источников брать фактуру и каким интерпретатором запускаться.

Формат — YAML (`voice.config.yaml`) или, если YAML почему-то неудобен, JSON
(`voice.config.json`) с той же схемой. Внешних зависимостей у пакета нет:
PyYAML используется, если он установлен, иначе включается встроенный
разборщик подмножества YAML, которого схеме конфига хватает (см. _mini_yaml).

    author: Имя Фамилия
    profile: ./profile
    sources:
      - type: mionika
        projects: [ключ-проекта, другой-ключ]
      - type: fact-registry
        path: путь/до/файла.md
        precedence: canon
      - type: files
        glob: "notes/**/*.md"
      - type: corpus
      - type: claude-sessions
        path: "~/.claude/projects/<ключ>/*.jsonl"
        optional: true
    python: путь/к/интерпретатору
    telegram:                 # необязательный блок, только для export_telegram.py
      env: ./telegram.env
      session: ./tg.session

Все относительные пути разрешаются от каталога, в котором лежит сам конфиг,
`~` раскрывается. Абсолютные пути берутся как есть.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

CONFIG_NAMES = ("voice.config.yaml", "voice.config.yml", "voice.config.json")

# Типы источников фактуры. Больше ничего не поддержано — неизвестный тип это
# ошибка конфига, а не «источник, который молча ничего не дал».
SOURCE_TYPES = {
    "mionika": "граф проектов mIOnika (ключи проектов в поле projects)",
    "fact-registry": "файл-реестр фактов (поле path)",
    "files": "файлы по маске (поле glob)",
    "corpus": "корпус самого профиля (profile/corpus)",
    "claude-sessions": "транскрипты сессий Claude Code по маске (поле path)",
}

# Значения поля precedence. `canon` разрешено не более чем одному источнику.
PRECEDENCE_VALUES = ("canon",)

TOP_LEVEL_KEYS = {"author", "profile", "sources", "python", "telegram"}

# Статусы источника.
CONFIGURED = "configured"          # сконфигурирован, всё на месте
NOT_CONFIGURED = "not_configured"  # источника нет в конфиге / не заполнены поля
MISSING_PATH = "missing_path"      # сконфигурирован, но путь/маска ничего не дают

STATUS_LABELS = {
    CONFIGURED: "сконфигурирован",
    NOT_CONFIGURED: "не сконфигурирован",
    MISSING_PATH: "сконфигурирован, но путь не существует",
}


class ConfigError(Exception):
    """Внятная ошибка конфига. Печатается как сообщение, а не как traceback."""


# --------------------------------------------------------------------------
# Минимальный разборщик YAML — чтобы у пакета не было обязательной внешней
# зависимости. Поддержано ровно то подмножество, на котором написана схема:
# отображения `ключ: значение`, вложенные блоки по отступу, блочные списки
# `- ...`, поточные списки `[a, b]`, кавычки, комментарии `#`, скаляры
# true/false/null/число/строка. Ни якорей, ни многострочных литералов,
# ни сложных ключей — если конфиг сложнее, ставится PyYAML или берётся JSON.
# --------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+[eE][+-]?\d+|\d+\.\d*[eE][+-]?\d+)$")


def _strip_comment(line: str) -> str:
    """Убирает хвостовой комментарий, не трогая `#` внутри кавычек."""
    out = []
    quote = None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_flow(body: str) -> list:
    """Делит содержимое `[...]` по запятым верхнего уровня."""
    items, cur, quote, depth = [], "", None, 0
    for ch in body:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        items.append(cur)
    return [i.strip() for i in items if i.strip()]


def _scalar(raw: str):
    s = raw.strip()
    if not s:
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(i) for i in _split_flow(s[1:-1])]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _key_value(line: str):
    """Делит `ключ: значение` по первому двоеточию вне кавычек."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == ":" and (i + 1 == len(line) or line[i + 1] in " \t"):
            return line[:i].strip().strip("\"'"), line[i + 1:].strip()
    return None


class _Lines:
    def __init__(self, text: str):
        self.rows = []
        for n, raw in enumerate(text.splitlines(), 1):
            body = _strip_comment(raw)
            if not body.strip():
                continue
            if body.lstrip().startswith("---"):
                continue
            self.rows.append((n, len(body) - len(body.lstrip()), body.strip()))
        self.i = 0

    def peek(self):
        return self.rows[self.i] if self.i < len(self.rows) else None


def _parse_block(lines: _Lines, indent: int):
    head = lines.peek()
    if head is None or head[1] < indent:
        return None
    if head[2].startswith("- "):
        return _parse_seq(lines, head[1])
    return _parse_map(lines, head[1])


def _parse_seq(lines: _Lines, indent: int) -> list:
    out = []
    while True:
        row = lines.peek()
        if row is None or row[1] != indent or not (
                row[2].startswith("- ") or row[2] == "-"):
            break
        lineno, _, body = row
        lines.i += 1
        item = body[2:].strip() if body.startswith("- ") else ""
        if not item:
            out.append(_parse_block(lines, indent + 1))
            continue
        kv = _key_value(item)
        if kv is None:
            out.append(_scalar(item))
            continue
        # Элемент-отображение: первая пара живёт в этой же строке, остальные —
        # ниже, с отступом до колонки, где начался ключ.
        inner_indent = indent + 2
        mapping = {}
        key, value = kv
        if value:
            mapping[key] = _scalar(value)
        else:
            nxt = lines.peek()
            mapping[key] = (_parse_block(lines, inner_indent + 1)
                            if nxt and nxt[1] > inner_indent else None)
        rest = _parse_map(lines, inner_indent, stop_below=True)
        mapping.update(rest or {})
        out.append(mapping)
    return out


def _parse_map(lines: _Lines, indent: int, stop_below: bool = False) -> dict:
    out = {}
    while True:
        row = lines.peek()
        if row is None or row[1] != indent:
            if row is not None and row[1] > indent and out and not stop_below:
                raise ConfigError(
                    f"строка {row[0]}: неожиданный отступ в конфиге — «{row[2]}»")
            break
        lineno, _, body = row
        if body.startswith("- "):
            break
        kv = _key_value(body)
        if kv is None:
            raise ConfigError(
                f"строка {lineno}: не разобрать «{body}» — ожидалось «ключ: значение»")
        lines.i += 1
        key, value = kv
        if value:
            out[key] = _scalar(value)
            continue
        nxt = lines.peek()
        if nxt is not None and nxt[1] > indent:
            out[key] = _parse_block(lines, nxt[1])
        else:
            out[key] = None
    return out


def _mini_yaml(text: str) -> dict:
    """Разбор подмножества YAML без внешних зависимостей."""
    lines = _Lines(text)
    if lines.peek() is None:
        return {}
    data = _parse_block(lines, lines.peek()[1])
    if lines.peek() is not None:
        row = lines.peek()
        raise ConfigError(f"строка {row[0]}: не разобрать «{row[2]}»")
    if not isinstance(data, dict):
        raise ConfigError("корень конфига должен быть отображением «ключ: значение»")
    return data


def parse_yaml(text: str) -> dict:
    """PyYAML, если он есть; иначе встроенный разборщик подмножества."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return _mini_yaml(text)
    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:  # pragma: no cover - зависит от установки
        raise ConfigError(f"не разобрать YAML: {e}")
    return data or {}


# --------------------------------------------------------------------------
# Конфиг
# --------------------------------------------------------------------------

def _expand(root: Path, value) -> Path:
    """Разрешает путь: `~` раскрывается, относительный — от каталога конфига."""
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (root / p).resolve()


class Source:
    """Один источник фактуры плюс его разрешённые пути."""

    def __init__(self, raw: dict, root: Path, profile: Path, index: int):
        self.index = index
        self.raw = dict(raw)
        self.type = raw.get("type")
        self.precedence = raw.get("precedence")
        self.optional = bool(raw.get("optional", False))
        self.projects = list(raw.get("projects") or []) if raw.get("projects") else []
        self.glob = raw.get("glob")
        self.path = _expand(root, raw["path"]) if raw.get("path") else None
        self._profile = profile
        self._root = root

    @property
    def label(self) -> str:
        if self.type == "mionika" and self.projects:
            return f"mionika[{', '.join(self.projects)}]"
        if self.glob:
            return f"{self.type}[{self.glob}]"
        if self.path:
            return f"{self.type}[{self.path}]"
        return str(self.type)

    @staticmethod
    def _split_pattern(base: Path, raw: str):
        """Делит путь-маску на неподвижное основание и шаблон.

        Маска может стоять в любом сегменте (`~/.claude/projects/*/x.jsonl`),
        поэтому основание — часть до первого сегмента с подстановочным знаком.
        """
        p = Path(raw).expanduser()
        parts = p.parts
        for i, part in enumerate(parts):
            if any(ch in part for ch in "*?["):
                head = Path(*parts[:i]) if i else Path(".")
                if not head.is_absolute():
                    head = base / head
                return head, "/".join(parts[i:])
        return (p if p.is_absolute() else base / p), None

    def _glob_hits(self) -> int:
        """Сколько файлов реально попадает под маску."""
        raw = self.glob or (str(self.raw.get("path")) if self.raw.get("path") else None)
        if not raw:
            return 0
        try:
            base, pattern = self._split_pattern(self._root, raw)
            if pattern is None:
                return 1 if base.exists() else 0
            if not base.exists():
                return 0
            return sum(1 for _ in base.glob(pattern))
        except (OSError, ValueError):
            return 0

    def status(self) -> dict:
        """Честный статус: сконфигурирован / нет / сконфигурирован, но пути нет."""
        t = self.type
        if t == "mionika":
            if not self.projects:
                return self._st(NOT_CONFIGURED, "не указан ни один ключ проекта")
            return self._st(CONFIGURED, f"проектов: {len(self.projects)}")
        if t == "fact-registry":
            if not self.path:
                return self._st(NOT_CONFIGURED, "не указан path")
            if not self.path.exists():
                return self._st(MISSING_PATH, f"нет файла {self.path}")
            return self._st(CONFIGURED, str(self.path))
        if t == "files":
            if not self.glob:
                return self._st(NOT_CONFIGURED, "не указан glob")
            hits = self._glob_hits()
            if not hits:
                return self._st(MISSING_PATH,
                                f"маска {self.glob} (от {self._root}) не дала файлов")
            return self._st(CONFIGURED, f"файлов по маске: {hits}")
        if t == "corpus":
            corpus = self._profile / "corpus"
            if not corpus.exists():
                return self._st(MISSING_PATH, f"нет каталога {corpus}")
            n = sum(1 for _ in corpus.glob("*.md"))
            if not n:
                return self._st(MISSING_PATH, f"в {corpus} нет файлов *.md")
            return self._st(CONFIGURED, f"постов в корпусе: {n}")
        if t == "claude-sessions":
            if not self.path:
                return self._st(NOT_CONFIGURED, "не указан path")
            hits = self._glob_hits()
            if not hits:
                return self._st(MISSING_PATH, f"маска {self.path} не дала файлов")
            return self._st(CONFIGURED, f"файлов сессий: {hits}")
        # сюда не попасть: тип провалидирован при загрузке
        return self._st(NOT_CONFIGURED, "неизвестный тип")

    def _st(self, status: str, detail: str) -> dict:
        return {
            "type": self.type,
            "label": self.label,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "detail": detail,
            "optional": self.optional,
            "precedence": self.precedence,
            "index": self.index,
        }


class Config:
    """Разобранный и провалидированный конфиг."""

    def __init__(self, data: dict, path: Path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.raw = data

        if not isinstance(data, dict):
            raise ConfigError(
                f"{self.path}: корень конфига должен быть отображением «ключ: значение»")

        self.unknown_keys = sorted(set(data) - TOP_LEVEL_KEYS)

        self.author = data.get("author")

        if not data.get("profile"):
            raise ConfigError(
                f"{self.path}: не задан обязательный ключ `profile` — каталог, где "
                f"лежат корпус, карты, stats.json и база правок. "
                f"Пример: profile: ./profile")
        self.profile = _expand(self.root, data["profile"])

        self.python = _expand(self.root, data["python"]) if data.get("python") else None

        tg = data.get("telegram") or {}
        if not isinstance(tg, dict):
            raise ConfigError(f"{self.path}: `telegram` должен быть отображением")
        self.telegram_env = _expand(self.root, tg["env"]) if tg.get("env") else None
        self.telegram_session = (_expand(self.root, tg["session"])
                                 if tg.get("session") else None)

        self.sources = self._build_sources(data.get("sources"))

    # --- пути профиля ---
    @property
    def corpus_dir(self) -> Path:
        return self.profile / "corpus"

    @property
    def articles_dir(self) -> Path:
        return self.profile / "articles"

    @property
    def edits_dir(self) -> Path:
        return self.profile / "edits"

    @property
    def stats_path(self) -> Path:
        return self.profile / "stats.json"

    @property
    def style_card(self) -> Path:
        return self.profile / "style-card.md"

    @property
    def articles_card(self) -> Path:
        return self.profile / "articles-card.md"

    @property
    def banned(self) -> Path:
        return self.profile / "banned.md"

    # --- валидация источников ---
    def _build_sources(self, raw_sources) -> list:
        if raw_sources is None:
            return []
        if not isinstance(raw_sources, list):
            raise ConfigError(
                f"{self.path}: `sources` должен быть списком, а не "
                f"{type(raw_sources).__name__}")
        out, canon = [], []
        for i, item in enumerate(raw_sources):
            if not isinstance(item, dict):
                raise ConfigError(
                    f"{self.path}: источник №{i + 1} должен быть отображением с "
                    f"ключом `type`, а не «{item}»")
            t = item.get("type")
            if not t:
                raise ConfigError(
                    f"{self.path}: у источника №{i + 1} нет обязательного поля "
                    f"`type`. Поддержаны: {', '.join(sorted(SOURCE_TYPES))}")
            if t not in SOURCE_TYPES:
                raise ConfigError(
                    f"{self.path}: неизвестный тип источника «{t}» "
                    f"(источник №{i + 1}). Поддержаны: "
                    + "; ".join(f"{k} — {v}" for k, v in sorted(SOURCE_TYPES.items())))
            prec = item.get("precedence")
            if prec is not None and prec not in PRECEDENCE_VALUES:
                raise ConfigError(
                    f"{self.path}: у источника «{t}» (№{i + 1}) значение "
                    f"`precedence: {prec}` не поддержано. Допустимо: "
                    f"{', '.join(PRECEDENCE_VALUES)} — или поле опускается.")
            if prec == "canon":
                canon.append((i + 1, t))
            out.append(Source(item, self.root, self.profile, i + 1))
        if len(canon) > 1:
            names = ", ".join(f"«{t}» (№{n})" for n, t in canon)
            raise ConfigError(
                f"{self.path}: `precedence: canon` стоит у {len(canon)} источников "
                f"({names}) — канон может быть ровно один, иначе непонятно, чей "
                f"факт побеждает при расхождении. Оставь canon у одного, у "
                f"остальных убери поле.")
        return out

    # --- статус ---
    def source_status(self) -> list:
        """Статус каждого источника: сконфигурирован / не сконфигурирован /
        сконфигурирован, но путь не существует.

        Нужен, чтобы скилл честно докладывал, какие источники реально
        отработали, а не считал пустой источник отработавшим."""
        return [s.status() for s in self.sources]

    def profile_status(self) -> list:
        """Статус артефактов профиля — что уже собрано, чего ещё нет."""
        items = [
            ("profile", self.profile, "каталог профиля"),
            ("corpus", self.corpus_dir, "корпус постов (*.md)"),
            ("articles", self.articles_dir, "длинная форма (*.md)"),
            ("edits", self.edits_dir, "база правок"),
            ("stats.json", self.stats_path, "статистики и пороги (собирает stats.py)"),
            ("style-card.md", self.style_card, "стилевая карта"),
            ("articles-card.md", self.articles_card, "карта длинной формы"),
            ("banned.md", self.banned, "список запретов"),
        ]
        out = []
        for name, path, what in items:
            exists = path.exists()
            out.append({
                "name": name,
                "path": str(path),
                "what": what,
                "status": CONFIGURED if exists else MISSING_PATH,
                "status_label": (STATUS_LABELS[CONFIGURED] if exists
                                 else "нет на диске"),
            })
        return out

    def format_status(self) -> str:
        lines = [f"конфиг: {self.path}",
                 f"автор: {self.author or '— не задан (ключ author)'}",
                 f"профиль: {self.profile}",
                 f"python: {self.python or '— не задан (ключ python)'}",
                 "",
                 "источники фактуры:"]
        if not self.sources:
            lines.append("  (не задано ни одного источника)")
        for s in self.source_status():
            opt = " · необязательный" if s["optional"] else ""
            canon = " · КАНОН" if s["precedence"] == "canon" else ""
            lines.append(f"  [{s['status_label']}] {s['label']}{canon}{opt}")
            lines.append(f"      {s['detail']}")
        lines += ["", "профиль:"]
        for p in self.profile_status():
            lines.append(f"  [{p['status_label']}] {p['name']} — {p['what']}")
            lines.append(f"      {p['path']}")
        if self.unknown_keys:
            lines += ["", "неизвестные ключи верхнего уровня (игнорируются): "
                      + ", ".join(self.unknown_keys)]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Поиск и загрузка
# --------------------------------------------------------------------------

def find_config(start: Path = None) -> Path:
    """Ищет voice.config.{yaml,yml,json} рядом и выше по дереву.

    Порядок: заданный каталог (по умолчанию — текущий), затем его родители,
    затем каталог самого пакета (tools/ и его родители). Возвращает None,
    если ничего не нашлось."""
    roots = []
    start = Path(start).resolve() if start else Path.cwd().resolve()
    roots.append(start)
    roots.extend(start.parents)
    here = Path(__file__).resolve().parent
    for p in [here] + list(here.parents):
        if p not in roots:
            roots.append(p)
    for d in roots:
        for name in CONFIG_NAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def load_config(path=None, start: Path = None) -> Config:
    """Читает конфиг и валидирует его. Кидает ConfigError с внятным текстом.

    path — явный путь (аргумент --config). Если не задан, берётся переменная
    окружения VOICE_KIT_CONFIG, иначе конфиг ищется рядом и выше по дереву.
    """
    if path is None:
        path = os.environ.get("VOICE_KIT_CONFIG") or None
    if path is None:
        found = find_config(start)
        if found is None:
            raise ConfigError(
                "не найден конфиг: ни один из "
                + ", ".join(CONFIG_NAMES)
                + " не лежит в текущем каталоге или выше. Скопируй "
                  "voice.config.example.yaml в voice.config.yaml рядом с "
                  "профилем и укажи путь через --config.")
        path = found
    path = Path(path).expanduser()
    if not path.is_file():
        raise ConfigError(f"нет файла конфига: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path}: не разобрать JSON — {e}")
    else:
        data = parse_yaml(text)
    return Config(data, path)


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """Общий для всех инструментов аргумент --config."""
    parser.add_argument(
        "--config", default=None,
        help="путь к voice.config.yaml (по умолчанию ищется рядом и выше по дереву)")


def load_or_die(path=None) -> Config:
    """Загрузка для CLI: ошибка конфига печатается сообщением, а не traceback."""
    try:
        return load_config(path)
    except ConfigError as e:
        raise SystemExit(f"ошибка конфига: {e}")


def main():
    ap = argparse.ArgumentParser(description="Статус конфига voice-kit")
    add_config_argument(ap)
    args = ap.parse_args()
    cfg = load_or_die(args.config)
    print(cfg.format_status())


if __name__ == "__main__":
    main()
