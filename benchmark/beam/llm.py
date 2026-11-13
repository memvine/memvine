"""The answer and judge model, called through OpenRouter.

One model answers and judges: openai/gpt-5.6-luna, with reasoning off.

A request is retried on a rate limit, a server error, a dropped connection or an unreadable HTTP
response: four attempts in all. A request that still fails, an empty reply, or a judge reply that is
not JSON raises; the caller scores that answer or verdict 0, as ExaBase's adapter does. A refusal of
the account itself (401, 402, 403: a wrong key, no credits) stops the run instead, since it would
otherwise score every question 0.
"""

import asyncio
import json
import os
import re

import httpx
from log import log

# BEAM_FAKE_MODEL=1 replaces the model with canned replies, to exercise the harness and a memory
# backend end to end with no OpenRouter account. Its scores mean nothing and the summary says so.
FAKE = os.environ.get("BEAM_FAKE_MODEL", "").strip() == "1"
# BEAM_LOCAL_JUDGE=<claude model alias> answers and judges through the local `claude -p` (a Claude
# subscription) instead of OpenRouter. Same prompts and scoring, different model: its scores are
# real but NOT comparable with published BEAM numbers, and the summary says so.
LOCAL_JUDGE = os.environ.get("BEAM_LOCAL_JUDGE", "").strip()
LOCAL_CONCURRENCY = int(os.environ.get("BEAM_LOCAL_CONCURRENCY", "2"))
MODEL = "fake" if FAKE else (f"claude-cli:{LOCAL_JUDGE} (local judge; not comparable)" if LOCAL_JUDGE else "openai/gpt-5.6-luna")
REASONING_EFFORT = "none"
BASE_URL = "https://openrouter.ai/api/v1"
TIMEOUT_SECONDS = 90
RETRY_DELAYS_SECONDS = (1, 2, 4)

_http: httpx.AsyncClient | None = None


class ModelError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def stops_the_run(error: BaseException) -> bool:
    """An account refusal from OpenRouter or Past, or a spent subscription: the setup is wrong (or
    out of budget), not this one question, so scoring it 0 would misreport the backend."""
    return getattr(error, "status", None) in (401, 402, 403) or getattr(error, "usage_limit", False)


USAGE_LIMIT = re.compile(r"hit your (session|usage|weekly) limit|usage limit reached", re.I)


_local_slots: asyncio.Semaphore | None = None


async def _local(prompt: str, system: str) -> str:
    """One `claude -p` call with no tools and no hooks; retried like the HTTP path."""
    global _local_slots
    if _local_slots is None:
        _local_slots = asyncio.Semaphore(LOCAL_CONCURRENCY)
    args = ["claude", "--print", "--no-session-persistence", "--output-format", "json", "--model", LOCAL_JUDGE,
            "--system-prompt", system, "--tools", "", "--settings", '{"disableAllHooks": true}']
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        async with _local_slots:
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "MEMVINE_HOOKS_DISABLED": "1"})
            out, err = await proc.communicate(prompt.encode())
        try:
            payload = json.loads(out.decode())
            if proc.returncode == 0 and not payload.get("is_error"):
                return str(payload.get("result", ""))
            detail = str(payload.get("result"))[:300]
        except json.JSONDecodeError:
            detail = (out or err).decode(errors="replace")[:300]
        if USAGE_LIMIT.search(detail):
            error = ModelError(f"claude -p failed: {detail}", retryable=False)
            error.usage_limit = True
            raise error
        if attempt == len(RETRY_DELAYS_SECONDS):
            raise ModelError(f"claude -p failed: {detail}", retryable=False)
        log(f"local judge call failed ({detail[:100]}); retrying in {RETRY_DELAYS_SECONDS[attempt] * 5}s")
        await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt] * 5)
    raise AssertionError("unreachable")


async def complete_text(prompt: str) -> str:
    """The model's complete free-form reply to one user message."""
    if FAKE:
        return f"[fake answer] {prompt[-400:]}"
    if LOCAL_JUDGE:
        text = await _local(prompt, "You are a helpful assistant.")
        if not text.strip():
            raise ValueError("The model returned an empty answer")
        return text
    response = await _post({
        "model": MODEL,
        "reasoning": {"effort": REASONING_EFFORT},
        "usage": {"include": True},
        "messages": [{"role": "user", "content": prompt}],
    })
    text = _reply_text(response)
    if not text.strip():
        raise ValueError("The model returned an empty answer")
    return text


async def complete_json(prompt: str, schema: dict) -> dict:
    """A reply held to a strict JSON schema, from a provider that enforces it."""
    if FAKE:
        return {"score": 0.5, "reason": "fake judge", "answer": "NO"}
    if LOCAL_JUDGE:
        fields = ", ".join(f'"{k}"' for k in schema["required"])
        text = await _local(prompt, f"Reply with ONLY a JSON object with exactly these keys: {fields}. No prose, no code fence.")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"judge reply has no JSON object: {text[:120]!r}")
        return json.loads(text[start:end + 1])
    response = await _post({
        "model": MODEL,
        "reasoning": {"effort": REASONING_EFFORT},
        "usage": {"include": True},
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "benchmark_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": schema["properties"],
                    "required": schema["required"],
                    "additionalProperties": False,
                },
            },
        },
        "provider": {"require_parameters": True},
    })
    text = _reply_text(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A reply wrapped in extra text still counts if it holds one JSON object.
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


async def close() -> None:
    if _http is not None:
        await _http.aclose()


# --- Transport -------------------------------------------------------------------------------

def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is required")
        _http = httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": f"Bearer {key}"},
                                  timeout=TIMEOUT_SECONDS)
    return _http


async def _post(payload: dict) -> dict:
    attempts = len(RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        try:
            return await _post_once(payload)
        except (httpx.HTTPError, ModelError, ValueError) as error:
            retryable = error.retryable if isinstance(error, ModelError) else True
            if not retryable or attempt == attempts - 1:
                raise
            log(f"model call failed ({str(error)[:120]}); retrying in {RETRY_DELAYS_SECONDS[attempt]}s")
            await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt])
    raise AssertionError("unreachable")


# OpenRouter holds back part of the account's credit for every request in flight, by its size. Many
# whole-conversation requests at once can exceed it (402, "in_flight_budget_exhausted") with credit
# to spare: BEAM_MODEL_CONCURRENCY bounds the calls in flight, and that refusal is retried.
MODEL_CONCURRENCY = int(os.environ.get("BEAM_MODEL_CONCURRENCY", "16"))
_model_slots: asyncio.Semaphore | None = None


async def _post_once(payload: dict) -> dict:
    global _model_slots
    if _model_slots is None:
        _model_slots = asyncio.Semaphore(MODEL_CONCURRENCY)
    async with _model_slots:
        response = await _client().post("/chat/completions", json=payload)
    if response.is_error:
        in_flight = response.status_code == 402 and "in_flight_budget_exhausted" in response.text
        raise ModelError(f"OpenRouter returned {response.status_code}: {response.text[:1_000]}",
                         retryable=in_flight or response.status_code == 429 or response.status_code >= 500,
                         status=None if in_flight else response.status_code)
    data = response.json()
    if not isinstance(data, dict):
        raise ModelError("OpenRouter returned a non-object response", retryable=True)
    if data.get("error"):
        raise ModelError(f"OpenRouter returned an error: {data['error']}", retryable=False)
    if not isinstance(data.get("choices"), list) or not data["choices"]:
        raise ModelError("OpenRouter returned no choices", retryable=False)
    return data


def _reply_text(response: dict) -> str:
    content = response["choices"][0]["message"]["content"]
    if content is None:
        return ""
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)
