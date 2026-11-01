"""Рендер транскрипта для классификатора: диаризация должна доезжать до модели.

Диаризация — самый дорогой шаг пайплайна (однопоточный воркер mode=call, ~1.3x
реального времени), но её результат лежал в `segments` и выбрасывался: клиент
брал из ответа только плоское поле `text`. Классификатор писал атомы в Мионику,
не зная, кто говорил, и атрибутировал обещания наугад.
"""
from ops_ingest.pipeline_client import render_transcript


def _payload(segments, *, diarized=True, text=None):
    if text is None:
        text = " ".join(s.get("text", "") for s in segments)
    return {"diarized": diarized, "text": text, "segments": segments}


def test_labels_each_speaker_turn():
    out = render_transcript(_payload([
        {"speaker": "SPEAKER_00", "text": "Как удобнее пошерить экран?"},
        {"speaker": "SPEAKER_01", "text": "Давай шерить, мне всё равно."},
    ]))
    assert out == ("[SPEAKER_00] Как удобнее пошерить экран?\n"
                   "[SPEAKER_01] Давай шерить, мне всё равно.")


def test_collapses_consecutive_turns_of_same_speaker():
    """Медиана 405 сегментов на звонок: без схлопывания метка на каждой реплике
    заметно раздувает промпт, а границы реплик от этого не становятся точнее."""
    out = render_transcript(_payload([
        {"speaker": "SPEAKER_00", "text": "Так, значит."},
        {"speaker": "SPEAKER_00", "text": "Тут вверху убираем."},
        {"speaker": "SPEAKER_01", "text": "Понял."},
    ]))
    assert out == ("[SPEAKER_00] Так, значит. Тут вверху убираем.\n"
                   "[SPEAKER_01] Понял.")


def test_falls_back_to_flat_text_when_not_diarized():
    """mode=short идёт без диаризации: метки там мусорные, подавать их нельзя."""
    out = render_transcript(_payload(
        [{"speaker": "SPEAKER_00", "text": "раз"}, {"speaker": "SPEAKER_00", "text": "два"}],
        diarized=False, text="раз два"))
    assert out == "раз два"


def test_falls_back_to_flat_text_when_segments_missing():
    assert render_transcript({"diarized": True, "text": "плоский текст"}) == "плоский текст"


def test_segment_without_speaker_does_not_crash():
    out = render_transcript(_payload([
        {"text": "без метки"},
        {"speaker": "SPEAKER_01", "text": "с меткой"},
    ]))
    assert "без метки" in out and "[SPEAKER_01] с меткой" in out


def test_empty_payload_is_empty_string():
    assert render_transcript({}) == ""
