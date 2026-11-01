from ops_ingest.context_prompt import build_extract_messages, EXTRACT_TOOL_NAME
from ops_ingest.context_prompt import build_route_messages, ROUTE_TOOL_NAME


def test_extract_messages_have_no_project_or_skeleton():
    p = build_extract_messages(transcript="разговор", today="2026-09-06")
    system_text = " ".join(b["text"] for b in p["system"])
    assert "Скелет" not in system_text
    assert "shortId" not in system_text
    assert p["tool_choice"]["name"] == EXTRACT_TOOL_NAME
    props = p["tools"][0]["input_schema"]["properties"]
    assert set(props) == {"atoms", "recency"}
    assert "node" not in props["atoms"]["items"]["properties"]


def test_route_messages_carry_skeleton_and_atoms_not_transcript():
    p = build_route_messages(
        project_title="Demo Bot",
        skeleton=[{"shortId": "MND-3", "title": "Решения", "children": ["старое решение"]}],
        atoms=[{"kind": "decision", "text": "новое решение"}],
        tasks=[{"title": "написать тезисы"}])
    system_text = " ".join(b["text"] for b in p["system"])
    assert "MND-3" in system_text
    assert "старое решение" in system_text
    user = p["messages"][0]["content"]
    assert "новое решение" in user
    assert "написать тезисы" in user
    props = p["tools"][0]["input_schema"]["properties"]
    assert set(props) == {"atom_nodes", "task_routes", "contradictions", "duplicates"}
    assert p["tool_choice"]["name"] == ROUTE_TOOL_NAME
