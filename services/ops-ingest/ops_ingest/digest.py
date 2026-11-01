"""Чистый маппинг Draft.commitments → задачи дайджеста. Без I/O."""
from .draft_schema import Draft

_ROUTING = {
    "стратегия": "owner",
    "производство": "agent:автоматизатор",
    "продажи": "owner",
    "консалтинг": "owner",
}


def default_owner(activity_type):
    return _ROUTING.get((activity_type or "").strip().lower(), "owner")


def _mk(item, *, project, source, owner=None, commitment_to="", soft=False):
    at = item.activity_type
    return {
        "title": item.what.strip(),
        "project": project or "",
        "activity_type": at or "",
        "owner": owner or item.owner_suggestion or default_owner(at),
        "commitment_to": commitment_to,
        "deadline": item.deadline,
        "soft": soft,
        "source": source,
    }


def commitments_to_digest_tasks(draft: Draft, job_id: str) -> list[dict]:
    project = draft.primary_project.key if draft.primary_project else ""
    source = f"call:{job_id}"
    c = draft.commitments
    tasks = []
    for it in c.by_owner:
        tasks.append(_mk(it, project=project, source=source, owner="owner"))
    for it in c.decisions_pending:
        tasks.append(_mk(it, project=project, source=source, owner="owner"))
    for it in c.delegations:
        tasks.append(_mk(it, project=project, source=source))
    for it in c.soft_offers:
        tasks.append(_mk(it, project=project, source=source, soft=True))
    for it in c.owed_to_owner:
        tasks.append(_mk(it, project=project, source=source, owner="owner",
                         commitment_to=(it.who or "").strip()))
    return tasks
