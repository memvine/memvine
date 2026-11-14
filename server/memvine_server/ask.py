"""Ask memory: the model's answer to a person's question, written only from what recall returned.

POST /v1/ask runs the recall POST /v1/recall runs, then calls `answer` with the result. The model
reads the briefing an agent would have received for the same question (ASK_SYSTEM says how to read
it) and nothing else. An ask never fails because the model does: with nothing recalled there is
nothing to answer from and the model is not called; with no model, a model error or a model slower
than settings.ask_timeout, the recall comes back without an answer and with the reason.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .config import settings
from .extraction.llm import LLM
from .extraction.prompts import ASK_SYSTEM, ask_user
from .models import AskResponse, RecallResponse

log = logging.getLogger("memvine.ask")

# An answer is a few sentences (ASK_SYSTEM caps it at 250 words); a reply cut off at this limit is an
# error from the model (llm.py), so it is reported as no answer, never shown half-written.
ASK_MAX_TOKENS = 700


async def answer(llm: LLM | None, question: str, recall: RecallResponse, *, as_of: datetime | None = None) -> AskResponse:
    """The main model's answer to `question` from `recall.briefing`. `as_of` is the moment the recall
    was asked about, when it was not now: the model judges "current" against it."""
    # On abstention the briefing is empty. The standing instructions still come back, but they are
    # rules for how to answer, not an answer to this question.
    if recall.abstained or not recall.briefing.strip():
        return AskResponse(answered=False, reason="nothing_relevant", recall=recall)
    if llm is None:
        return AskResponse(answered=False, reason="model_unavailable", recall=recall)
    prompt = ask_user(question, recall.briefing, (as_of or datetime.now(UTC)).strftime("%-d %b %Y"))
    try:
        text = await asyncio.wait_for(llm.complete(ASK_SYSTEM, prompt, max_tokens=ASK_MAX_TOKENS), settings.ask_timeout)
    except Exception as exc:  # noqa: BLE001  whatever the backend raised, the recall is still worth returning
        log.warning("ask got no answer from the model: %s: %s", type(exc).__name__, exc)
        return AskResponse(answered=False, reason="model_unavailable", recall=recall)
    text = (text or "").strip()
    if not text:
        log.warning("ask got an empty answer from the model")
        return AskResponse(answered=False, reason="model_unavailable", recall=recall)
    return AskResponse(answer=text, answered=True, recall=recall)
