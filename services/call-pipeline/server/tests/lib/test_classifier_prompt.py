from app.lib.classifier_prompt import build_messages


def test_build_messages_has_cache_breakpoints():
    msgs = build_messages(
        user_context_summary="Ирина, руководитель студии",
        projects=[{"key": "mz", "name": "MZ", "description": "tracking"}],
        transcript_text="Привет, как дела",
    )
    sys = msgs["system"]
    assert isinstance(sys, list)
    cached = [b for b in sys if b.get("cache_control")]
    assert len(cached) >= 2  # system instructions + user context + projects


def test_user_message_has_only_transcript():
    msgs = build_messages(
        user_context_summary="x",
        projects=[],
        transcript_text="full transcript",
    )
    assert "full transcript" in msgs["messages"][0]["content"]
    assert msgs["messages"][0]["role"] == "user"


def test_system_includes_user_context():
    msgs = build_messages(
        user_context_summary="Ирина, руководитель студии",
        projects=[],
        transcript_text="t",
    )
    text_blocks = [b["text"] for b in msgs["system"]]
    joined = " ".join(text_blocks)
    assert "Ирина, руководитель студии" in joined


def test_system_includes_projects_snapshot():
    msgs = build_messages(
        user_context_summary="x",
        projects=[{"key": "demo-crm", "name": "Demo CRM",
                   "description": "trekking"}],
        transcript_text="t",
    )
    text_blocks = [b["text"] for b in msgs["system"]]
    joined = " ".join(text_blocks)
    assert "demo-crm" in joined


def test_system_instructions_route_through_submit_draft_tool():
    msgs = build_messages(
        user_context_summary="x", projects=[], transcript_text="t"
    )
    instructions = msgs["system"][0]["text"]
    assert "submit_draft" in instructions


def test_messages_include_submit_draft_tool():
    msgs = build_messages(
        user_context_summary="x", projects=[], transcript_text="t"
    )
    assert "tools" in msgs
    tools = msgs["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "submit_draft"
    schema = tools[0]["input_schema"]
    assert "properties" in schema
    assert "summary" in schema["properties"]


def test_tool_choice_forces_submit_draft():
    msgs = build_messages(
        user_context_summary="x", projects=[], transcript_text="t"
    )
    assert msgs["tool_choice"]["type"] == "tool"
    assert msgs["tool_choice"]["name"] == "submit_draft"
