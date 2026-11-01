from ops_ingest.dedup import recording_key


# --- recording_key ---

def test_recording_key_strips_short_suffix():
    assert recording_key("abc:short") == "abc"


def test_recording_key_strips_call_suffix():
    assert recording_key("abc:call") == "abc"


def test_recording_key_no_suffix_unchanged():
    assert recording_key("abc") == "abc"


def test_recording_key_none_returns_none():
    assert recording_key(None) is None


def test_recording_key_empty_returns_none():
    assert recording_key("") is None


def test_recording_key_multi_colon_strips_only_suffix():
    assert recording_key("a:b:short") == "a:b"
    assert recording_key("a:b:call") == "a:b"


def test_recording_key_other_suffix_unchanged():
    """Non :short/:call suffixes must NOT be stripped."""
    assert recording_key("abc:other") == "abc:other"
