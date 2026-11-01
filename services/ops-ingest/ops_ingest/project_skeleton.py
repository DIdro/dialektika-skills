"""Чтение скелета проекта из Mionika REST для structure-discovering апдейта контекста."""
import asyncio
import httpx

REST_BASE = "https://mionika.ryefield.com"


def build_skeleton(nodes, node_child_titles) -> list[dict]:
    out = []
    for n in nodes:
        sid = n.get("shortId")
        out.append({"id": n.get("id"), "shortId": sid, "title": n.get("title") or "",
                    "children": list(node_child_titles.get(sid, []))})
    return out


def _get(access_token, project_id, atom_type):
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{REST_BASE}/api/v1/atoms", params={"type": atom_type, "limit": 500},
                  headers={"Authorization": f"Bearer {access_token}", "X-Project-Id": project_id})
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, list) else (j.get("atoms") or j.get("data") or j.get("items") or [])


def _parent_short(atom) -> str | None:
    # contains-родитель приходит в links; ops-ingest читает плоско — берём metadata.parentShortId,
    # иначе None (дочерний дайджест best-effort).
    md = atom.get("metadata") or {}
    return md.get("parentShortId") or md.get("parent")


async def fetch_skeleton(access_token, project_id) -> list[dict]:
    def work():
        nodes = _get(access_token, project_id, "mindmap_node")
        children = _get(access_token, project_id, "note") + _get(access_token, project_id, "decision")
        by_parent = {}
        for ch in children:
            p = _parent_short(ch)
            if p:
                by_parent.setdefault(p, []).append((ch.get("title") or "")[:80])
        node_dicts = [{"shortId": n.get("shortId"), "id": n.get("id"), "title": n.get("title")} for n in nodes]
        return build_skeleton(node_dicts, by_parent)
    return await asyncio.to_thread(work)
