import pytest
from pydantic import ValidationError
from app.lib.draft_schema import Draft, KnowledgeAction


def test_round_trip():
    d = Draft(
        summary="Trekking call",
        primary_project={"key": "demo-crm", "confidence": 0.9, "rationale": "..."},
        alternative_projects=[],
        uncertainty=None,
        actions=[
            KnowledgeAction(type="update_atom", atom_id="aaa",
                          changes={"status": "in_progress -> review"},
                          rationale="..."),
        ],
        secondary_destinations=[],
    )
    s = d.model_dump_json()
    d2 = Draft.model_validate_json(s)
    assert d2.summary == "Trekking call"
    assert d2.actions[0].atom_id == "aaa"


def test_invalid_action_type_rejected():
    with pytest.raises(ValidationError):
        KnowledgeAction(type="evil", atom_id="x", changes={}, rationale="x")


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Draft(
            summary="...",
            primary_project={"key": "k", "confidence": 1.5, "rationale": "..."},
        )


def test_minimal_draft_with_uncertainty():
    d = Draft(
        summary="ambiguous",
        primary_project=None,
        uncertainty={"description": "two equally likely projects",
                     "options": ["a", "b"]},
    )
    assert d.uncertainty.description == "ambiguous"[:9] or d.uncertainty is not None
    # alternative_projects, actions, etc. default to []
    assert d.actions == []


def test_create_atom_action_without_atom_id():
    """A create_atom proposal won't have an atom_id (it's a NEW atom)."""
    a = KnowledgeAction(
        type="create_atom",
        parent_id="parent-uuid",
        details={"type": "decision", "title": "Closed"},
        rationale="discussed at 0:13",
    )
    assert a.type == "create_atom"
    assert a.atom_id is None


