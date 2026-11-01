from anthropic import AsyncAnthropic
from .draft_schema import Draft
from .prompt import build_messages, DRAFT_TOOL_NAME

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000


def extract_draft(tool_input: dict) -> Draft:
    return Draft.model_validate(tool_input)


def _tool_input(content_blocks) -> dict:
    for b in content_blocks:
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == DRAFT_TOOL_NAME:
            return b.input
    raise RuntimeError("response missing submit_draft tool_use")


async def classify(transcript_text: str, projects: list[dict], api_key: str,
                   model: str = MODEL, extra_instruction: str | None = None) -> Draft:
    payload = build_messages(projects=projects, transcript_text=transcript_text)
    if extra_instruction:
        # инструкция владельца дописывается в пользовательское сообщение перед призывом
        # вызвать инструмент — переразбор (review_actions.reparse) идёт с правкой владельца.
        user = payload["messages"][0]["content"]
        marker = "\n\nВызови submit_draft."
        head = user[:-len(marker)] if user.endswith(marker) else user
        payload["messages"][0]["content"] = (
            f"{head}\n\nИнструкция владельца записи:\n{extra_instruction}\n\nВызови submit_draft.")
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=model, max_tokens=MAX_TOKENS, system=payload["system"],
        messages=payload["messages"], tools=payload["tools"], tool_choice=payload["tool_choice"])
    return extract_draft(_tool_input(resp.content))
