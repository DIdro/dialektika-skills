from ops_ingest.pipeline_client import pick_short_jobs, transcript_facts


def test_pick_short_jobs_filters_mode():
    jobs = [{"job_id": "a", "mode": "short"}, {"job_id": "b", "mode": "call"},
            {"job_id": "c", "mode": "short"}]
    assert [j["job_id"] for j in pick_short_jobs(jobs)] == ["a", "c"]


def test_transcript_facts_counts_speakers_and_unknown():
    payload = {"diarized": True, "segments": [
        {"speaker": "SPEAKER_00", "text": "а"},
        {"speaker": "SPEAKER_01", "text": "б"},
        {"speaker": "UNKNOWN", "text": "в"},
        {"speaker": "SPEAKER_00", "text": "г"},
    ]}
    f = transcript_facts(payload)
    assert f["diarized"] is True
    assert f["speaker_count"] == 2          # UNKNOWN не спикер
    assert round(f["unknown_share"], 2) == 0.25


def test_transcript_facts_without_diarization():
    f = transcript_facts({"diarized": False, "segments": [], "text": "плоский"})
    assert f == {"diarized": False, "speaker_count": 0, "unknown_share": 0.0}
