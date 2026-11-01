from ops_ingest.telegram_bot import extract_text_messages


def test_extract_text_messages_filters_non_text_and_captures_reply_to():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "привет"}},
        {"update_id": 2, "message": {"chat": {"id": 42}}},          # no text
        {"update_id": 3, "edited_message": {"chat": {"id": 42}, "text": "x"}},  # not 'message'
        {"update_id": 4, "message": {"chat": {"id": 99}, "text": "2 на Валю",
                                     "reply_to_message": {"message_id": 555}}},
    ]
    out = extract_text_messages(updates)
    assert out == [
        {"update_id": 1, "chat_id": 42, "text": "привет", "reply_to": None},
        {"update_id": 4, "chat_id": 99, "text": "2 на Валю", "reply_to": 555},
    ]
