import asyncio
import json
import logging
import os

from anthropic import AsyncAnthropic
from sqlalchemy.orm import Session

from ..db import queries as q
from ..db import session as session_mod
from ..db.models import Job, User
from ..lib import storage
from ..lib.classifier_prompt import build_messages
from ..lib.context_providers.file import FileProvider
from ..lib.draft_schema import Draft, Uncertainty
from ._common import fail_job

POLL_INTERVAL_SEC = 3.0
MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4000

_log = logging.getLogger(__name__)


def _client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _user_config_from_row(user: User) -> dict:
    return {
        "context_summary": user.context_summary or "",
        "system_prompt_override": user.system_prompt_override,
    }


def _extract_tool_input(content_blocks) -> dict:
    """Find the submit_draft tool_use block and return its input dict."""
    for b in content_blocks:
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == "submit_draft":
            return b.input
    raise RuntimeError("Anthropic response missing submit_draft tool_use block")


async def process_one() -> bool:
    s: Session = session_mod.SessionLocal()
    try:
        job = q.claim_next_job(
            s, modes=["call", "short"],
            from_status="transcribed",
            to_status="processing_classifier",
        )
        if job is None:
            return False
        try:
            q.record_event(s, job.id, event="started", stage="classifier")
            user = s.get(User, job.user_id)
            user_cfg = _user_config_from_row(user)
            transcript_data = json.loads(
                open(job.transcript_path, encoding="utf-8").read()
            )

            provider = FileProvider()
            context = {"projects": []}
            if provider.is_available(user_cfg):
                context = await provider.fetch_context(transcript_data["text"], user_cfg)

            payload = build_messages(
                user_context_summary=user_cfg["context_summary"],
                projects=context.get("projects", []),
                transcript_text=transcript_data["text"],
            )
            client = _client()
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=payload["system"],
                messages=payload["messages"],
                tools=payload["tools"],
                tool_choice=payload["tool_choice"],
            )
            tool_input = _extract_tool_input(resp.content)
            draft = Draft.model_validate(tool_input)

            # If the project context could not be fetched, the draft was
            # produced BLIND — it cannot route to any project. Do not let that
            # pass silently (it did for days). Log loudly and stamp the draft
            # so the reviewer sees it instead of trusting a
            # confidently-empty result.
            ctx_error = context.get("error")
            if ctx_error:
                _log.error(
                    "Project context UNAVAILABLE for user %s job %s "
                    "(%s) — draft produced without project list",
                    user.id, job.id, ctx_error,
                )
                warn = (
                    f"⚠ Список проектов был НЕДОСТУПЕН ({ctx_error}). "
                    "Маршрут по проектам не определялся — перепроверь вручную."
                )
                if draft.uncertainty:
                    draft.uncertainty.description = (
                        warn + " | " + draft.uncertainty.description
                    )
                else:
                    draft.uncertainty = Uncertainty(
                        description=warn,
                        options=[
                            "Перепроверить маршрут после восстановления списка",
                        ],
                    )

            dpath = storage.draft_path(user.id, job.id)
            dpath.parent.mkdir(parents=True, exist_ok=True)
            dpath.write_text(
                draft.model_dump_json(indent=2), encoding="utf-8"
            )

            j2 = s.get(Job, job.id)
            j2.draft_path = str(dpath)
            j2.status = "drafted"
            s.commit()
            q.record_event(s, job.id, event="completed", stage="classifier")
            return True
        except Exception as e:
            fail_job(s, job.id, stage="classifier", error=str(e))
            return True
    finally:
        s.close()


def recover_stranded() -> None:
    """См. whisper_call.recover_stranded. У классификатора та же пара статусов:
    брошенная строка застревает в processing_classifier и черновика не получит."""
    s: Session = session_mod.SessionLocal()
    try:
        ids = q.requeue_stranded(s, modes=["call", "short"],
                                 from_status="processing_classifier",
                                 to_status="transcribed",
                                 terminal_status="failed_classifier",
                                 stage="classifier")
        if ids:
            logging.getLogger(__name__).warning(
                "requeued %d job(s) stranded by a previous worker: %s", len(ids), ", ".join(ids))
    finally:
        s.close()


async def run_loop():
    recover_stranded()
    while True:
        if not await process_one():
            await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(run_loop())
