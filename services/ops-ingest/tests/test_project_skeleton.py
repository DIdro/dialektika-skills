from ops_ingest.project_skeleton import build_skeleton


def test_build_skeleton_attaches_child_titles():
    nodes = [{"shortId": "MND-8", "id": "u8", "title": "Decision Log — Расписание"},
             {"shortId": "MND-19", "id": "u19", "title": "Risk Log — Расписание"}]
    node_child_titles = {"MND-8": ["Выложить ТЗ на обсуждение"], "MND-19": []}
    sk = build_skeleton(nodes, node_child_titles)
    by = {n["shortId"]: n for n in sk}
    assert by["MND-8"]["title"] == "Decision Log — Расписание"
    assert by["MND-8"]["children"] == ["Выложить ТЗ на обсуждение"]
    assert by["MND-19"]["children"] == []
    assert by["MND-8"]["id"] == "u8"
