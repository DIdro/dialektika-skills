import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator

# Вложенные поля, которые модель иногда отдаёт СТРОКОЙ с JSON вместо объекта.
# Замерено на доборе 78 звонков 06.09: 2 отказа из 78 (≈3%) — один на
# commitments, один на uncertainty. Без починки такой звонок роняет разбор,
# а ограничителя повторов нет: он крутился бы в цикле, пока не заметят по логам.
_STRUCTURED = ("commitments", "uncertainty", "primary_project", "alternative_projects")

ActivityType = Literal["стратегия", "производство", "продажи", "консалтинг"]


class ProjectGuess(BaseModel):
    key: str
    confidence: float = Field(ge=0, le=1)
    rationale: str


class Uncertainty(BaseModel):
    description: str
    options: list[str] = []


class CommitmentItem(BaseModel):
    what: str = Field(description="The concrete commitment / task / decision, in one line.")
    who: str | None = Field(default=None, description="Counterparty / delegate.")
    deadline: str | None = Field(default=None, description="Any date/time named or implied.")
    quote: str | None = Field(default=None, description="Short supporting moment.")
    activity_type: ActivityType | None = Field(
        default=None, description="Proposed board activity type for actionable items; null if unsure.")
    owner_suggestion: str | None = Field(
        default=None, description="'owner' | 'person:<name>' | 'agent:<type>'; null if unsure.")


class Commitments(BaseModel):
    by_owner: list[CommitmentItem] = Field(default_factory=list)
    owed_to_owner: list[CommitmentItem] = Field(default_factory=list)
    decisions_pending: list[CommitmentItem] = Field(default_factory=list)
    deadlines: list[CommitmentItem] = Field(default_factory=list)
    soft_offers: list[CommitmentItem] = Field(default_factory=list)
    delegations: list[CommitmentItem] = Field(default_factory=list)


class Draft(BaseModel):
    summary: str
    client_summary: str | None = Field(
        default=None,
        description="Короткое сообщение ДЛЯ ЗАКАЗЧИКА (не для владельца): вежливое, от первого лица "
                    "владельца к клиенту, итоги+следующие шаги, готовое к пересылке. Null если звонок внутренний.")
    commitments: Commitments = Field(default_factory=Commitments)
    primary_project: ProjectGuess | None = None
    alternative_projects: list[ProjectGuess] = []
    uncertainty: Uncertainty | None = None
    owner_identified: bool | None = Field(
        default=None,
        description="True — владелец опознан среди спикеров; False — разметка была, "
                    "но опознать не удалось; null — разметки не было.")

    @model_validator(mode="before")
    @classmethod
    def _parse_stringified(cls, data):
        """Разобрать вложенные поля, пришедшие строкой с JSON.

        Модель изредка кладёт в структурное поле не объект, а его JSON-строку.
        Схема такой драфт отвергала целиком, и звонок оставался неразобранным.
        Разбираем строку молча; если она не разбирается — оставляем как есть,
        пусть валидация ругается по существу, а не на подмену типа.
        Бывает и лишняя скобка в хвосте (`{...}]`, переразбор 28.10) — берём
        ведущий целый JSON, хвост отбрасываем.
        """
        if not isinstance(data, dict):
            return data
        for key in _STRUCTURED:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    data[key], _ = json.JSONDecoder().raw_decode(value.strip())
                except (ValueError, TypeError):
                    pass
        return data


def alternatives_of(draft) -> list[dict]:
    """Альтернативные кандидаты классификатора для строки очереди.

    Хранятся рядом с выбором, а не вместо него: при низкой уверенности владельцу
    нужно видеть, между чем модель колебалась, — иначе смена проекта делается
    вслепую, по списку всех проектов сразу.
    """
    return [{"key": p.key, "confidence": p.confidence, "rationale": p.rationale}
            for p in (draft.alternative_projects or [])]


def uncertainty_text(draft) -> str:
    """Неуверенность модели одной строкой: описание плюс названные ею варианты.

    Варианты приклеиваются к тексту, а не выбрасываются: это то, что модель сама
    назвала непонятным, и терять его молча нельзя.
    """
    u = draft.uncertainty
    if u is None:
        return ""
    opts = ("; варианты: " + ", ".join(u.options)) if u.options else ""
    return f"{u.description}{opts}"
