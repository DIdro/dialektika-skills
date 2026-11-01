from typing import Literal

from pydantic import BaseModel, Field


class ProjectGuess(BaseModel):
    key: str
    confidence: float = Field(ge=0, le=1)
    rationale: str


class Uncertainty(BaseModel):
    description: str
    options: list[str]


class KnowledgeAction(BaseModel):
    type: Literal["create_atom", "update_atom", "supersede_atom", "link_atoms"]
    atom_id: str | None = None
    parent_id: str | None = None
    changes: dict | None = None
    details: dict | None = None
    rationale: str


class SecondaryDestination(BaseModel):
    project_key: str
    reason: str
    draft_summary: str


class Draft(BaseModel):
    summary: str
    primary_project: ProjectGuess | None = None
    alternative_projects: list[ProjectGuess] = []
    uncertainty: Uncertainty | None = None
    actions: list[KnowledgeAction] = []
    secondary_destinations: list[SecondaryDestination] = []
