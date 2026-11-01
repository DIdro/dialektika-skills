_RECORDING_SUFFIXES = (":short", ":call")


def recording_key(dedupe_key: "str | None") -> "str | None":
    """Strip a trailing :short or :call suffix from dedupe_key to get the bare recording sha.

    Examples:
        recording_key("abc:short") == "abc"
        recording_key("abc:call")  == "abc"
        recording_key("abc")       == "abc"
        recording_key(None)        is None
        recording_key("")          is None
    Other suffixes are left intact.
    """
    if not dedupe_key:
        return None
    for suffix in _RECORDING_SUFFIXES:
        if dedupe_key.endswith(suffix):
            return dedupe_key[: -len(suffix)]
    return dedupe_key
