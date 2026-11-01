"""Short call title for a diarized transcript, derived from its content.

The Telemost bot delivers each diarized transcript as a Telegram document.
Without a label they're indistinguishable — random-hash filenames and an
identical caption — so you can't tell which call a transcript belongs to. This
asks gpt-4o-mini for a short human-readable title from the transcript text,
which the bot puts in the caption, at the top of the .txt, and in the filename.

`summarize_title` is best-effort: any failure returns None and the caller falls
back to the plain caption. The formatting helpers (`clean_title`, `caption_for`,
`file_header`, `slugify`) are pure so they're unit-tested without a network call.

Reads OPENAI_API_KEY — already set for the voice bot's cleanup pass.
"""
import logging
import os
import re

_log = logging.getLogger("telemost_title")

MODEL = "gpt-4o-mini"
_MAX_CHARS = 12000  # enough context for a title; keeps the call fast and cheap

_SYSTEM = (
    "Ты делаешь короткий заголовок для расшифровки звонка, чтобы по нему сразу "
    "было понятно, что это за звонок. На вход — расшифровка (возможно со "
    "спикерами).\n"
    "Правила:\n"
    "— 3–7 слов, одной строкой.\n"
    "— Отрази главную тему и, если ясно из текста, проект или участников "
    "(напр. «Мир Здоровья — планёрка по CRM»).\n"
    "— Без кавычек, без точки в конце, без префиксов вроде «Заголовок:».\n"
    "— Язык — русский.\n"
    "Верни только заголовок."
)


def clean_title(raw: str | None) -> str | None:
    """Normalize a model reply into a single bare title line, or None if empty.

    Strips a leading "Заголовок:"-style label, wrapping quotes/guillemets, a
    trailing period, and keeps only the first line — defending against the
    small ways gpt-4o-mini ignores the "just the title" instruction.
    """
    if not raw or not raw.strip():
        return None
    title = raw.strip().splitlines()[0].strip()
    title = re.sub(r"^(заголовок|название|title)\s*[:—-]\s*", "", title,
                   flags=re.IGNORECASE)
    title = title.strip().strip('"“”«»').strip()
    title = title.rstrip(".").strip()
    return title or None


def caption_for(title: str | None, n: int) -> str:
    """Telegram caption under the transcript document."""
    base = f"Расшифровка со спикерами ({n} чел.)"
    return f"📝 {title}\n{base}" if title else f"📝 {base}"


def file_header(title: str | None) -> str:
    """Title line prepended inside the .txt body (empty string if no title)."""
    return f"{title}\n\n" if title else ""


def slugify(s: str | None, fallback: str, maxlen: int = 40) -> str:
    """Filesystem-safe, identifiable transcript filename stem.

    Keeps Unicode word chars (so Cyrillic titles stay readable), collapses
    everything else to underscores, truncates, and falls back when the title
    yields nothing usable.
    """
    slug = re.sub(r"[^\w]+", "_", (s or ""), flags=re.UNICODE).strip("_")
    slug = slug[:maxlen].strip("_")
    return slug or fallback


async def summarize_title(text: str | None) -> str | None:
    """Short title for a call from its transcript. None on empty/failure."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = await client.chat.completions.create(
            model=MODEL,
            temperature=0.3,
            max_tokens=40,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text[:_MAX_CHARS]},
            ],
        )
    except Exception as e:
        _log.warning("title generation failed: %s", e)
        return None
    return clean_title(resp.choices[0].message.content)
