# -*- coding: utf-8 -*-
"""Нарезка выгрузки канала в файлы корпуса.

    python build_corpus.py --src out/raw.jsonl
    python build_corpus.py --config ../voice.config.yaml --src out/raw.jsonl

Каталог корпуса берётся из конфига: `profile` + `/corpus`.
"""

import argparse
import json
from pathlib import Path

from config import add_config_argument, load_or_die

HERE = Path(__file__).parent


def corpus_filename(rec: dict) -> str:
    day = rec["date"][:10]
    return f"{day}-{rec['id']}.md"


def render_post(rec: dict) -> str:
    day = rec["date"][:10]
    text = rec["text"].rstrip("\n")
    return (
        "---\n"
        f"date: {day}\n"
        f"id: {rec['id']}\n"
        "genre: unlabeled\n"
        "---\n\n"
        f"{text}\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="out/raw.jsonl")
    add_config_argument(ap)
    args = ap.parse_args()

    corpus_dir = load_or_die(args.config).corpus_dir

    src = HERE / args.src if not Path(args.src).is_absolute() else Path(args.src)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    with src.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            (corpus_dir / corpus_filename(rec)).write_text(render_post(rec), encoding="utf-8")
            written += 1

    print(f"записано постов: {written} → {corpus_dir}")


if __name__ == "__main__":
    main()
