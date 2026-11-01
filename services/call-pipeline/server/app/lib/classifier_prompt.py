import json

from .draft_schema import Draft


SYSTEM_INSTRUCTIONS = """\
You are a classifier for call transcripts. Your job:

1. Identify the primary project the call belongs to (or mark uncertainty).
2. Propose specific knowledge-base updates as a draft (NOT to be applied automatically).
3. Submit your output via the `submit_draft` tool — its input_schema is the
   single source of truth for field names, types, and which fields are
   required. Do not invent field names.

Rules:
- Use only project keys present in the projects snapshot.
- Be concrete: cite specific moments from the transcript in `rationale`.
- If you're unsure, fill `uncertainty` (with `description` and `options`); leave `primary_project` null.
- Never invent atom ids — leave `atom_id` null and use `type=create_atom` for new atoms.
- Always call the `submit_draft` tool exactly once.
"""


# The Draft Pydantic model's JSON Schema is the contract Anthropic must produce.
# Generated lazily so this stays in sync with draft_schema.py without a manual copy.
DRAFT_TOOL_NAME = "submit_draft"


def _draft_tool_schema() -> dict:
    schema = Draft.model_json_schema()
    return {
        "name": DRAFT_TOOL_NAME,
        "description": "Submit the classifier's draft of knowledge-base updates. "
                        "Call this exactly once per transcript.",
        "input_schema": schema,
    }


def build_messages(
    *,
    user_context_summary: str,
    projects: list[dict],
    transcript_text: str,
) -> dict:
    """Returns {'system': [...], 'messages': [...], 'tools': [...]} for Anthropic API.

    Static parts (system + user context + projects + tools) get
    cache_control={'type':'ephemeral'} so they hit the prompt cache on the
    next call within ~5 minutes (saves ~70% on input tokens). Transcript is
    fresh per call and lives in `messages`.

    The `tools` list contains a single `submit_draft` tool whose input_schema
    is the Pydantic Draft schema. The caller is expected to pass these to
    `client.messages.create(..., tools=tools, tool_choice={"type":"tool", "name":"submit_draft"})`.
    """
    projects_block = (
        "Active projects:\n"
        + json.dumps(projects, ensure_ascii=False, indent=2)
    )
    user_block = (
        "User context (about the speaker, persistent):\n"
        + user_context_summary
    )
    return {
        "system": [
            {"type": "text", "text": SYSTEM_INSTRUCTIONS,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user_block,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": projects_block,
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [
            {"role": "user",
             "content": (
                 f"Transcript to classify:\n\n{transcript_text}\n\n"
                 "Call submit_draft with your draft now."
             )},
        ],
        "tools": [_draft_tool_schema()],
        "tool_choice": {"type": "tool", "name": DRAFT_TOOL_NAME},
    }
