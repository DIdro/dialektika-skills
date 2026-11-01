from ops_ingest.digest import commitments_to_digest_tasks
from ops_ingest.draft_schema import Draft, Commitments, CommitmentItem, ProjectGuess


def _draft():
    return Draft(
        summary="s",
        primary_project=ProjectGuess(key="demo-bot", confidence=0.9, rationale="r"),
        commitments=Commitments(
            by_owner=[CommitmentItem(what="Прислать презентацию", activity_type="продажи")],
            owed_to_owner=[CommitmentItem(what="Вернуться с датами", who="Лев")],
            soft_offers=[CommitmentItem(what="Подумать про вебинар")],
        ),
    )


def test_flattens_commitments_with_source_and_owner():
    tasks = commitments_to_digest_tasks(_draft(), job_id="j1")
    assert [t["title"] for t in tasks] == [
        "Прислать презентацию", "Подумать про вебинар", "Вернуться с датами"]
    assert all(t["source"] == "call:j1" for t in tasks)
    assert tasks[0]["owner"] == "owner"
    assert tasks[2]["commitment_to"] == "Лев"
    assert tasks[1]["soft"] is True


def test_project_from_primary_guess():
    tasks = commitments_to_digest_tasks(_draft(), job_id="j1")
    assert all(t["project"] == "demo-bot" for t in tasks)
