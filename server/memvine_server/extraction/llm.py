"""One interface over the model backends the synthesis pipeline can use.

Every call is "system prompt + user prompt in, text out". Callers parse JSON
out of the text themselves (see extract.parse_json), so a backend never has to
know what shape is expected.

Backends:
  AnthropicLLM  - the Anthropic SDK. Production, and the one the memory check is run on.
  OpenAICompatibleLLM - any server that speaks the OpenAI chat completions API: OpenAI,
                  OpenRouter, Gemini, Groq, a local Ollama or LM Studio.
  ClaudeCliLLM  - `claude -p`, billed to the developer's Claude subscription.
                  Local development only; not for a hosted deployment.
  FakeLLM       - canned responses keyed by a marker in the prompt. Tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import Settings, settings

RATE_LIMIT_HINT = re.compile(r"rate.?limit|usage limit|limit reached|too many requests|overloaded|429|529", re.IGNORECASE)


class LLM(Protocol):
    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str: ...


class LLMError(RuntimeError):
    pass


class LLMRateLimited(LLMError):
    """The provider asked us to slow down. The job is retried after a long pause, not counted
    against its attempts."""


class AnthropicLLM:
    # A caller's max_tokens is the room its answer needs. Current Claude models think before they
    # answer, and what they think counts toward max_tokens: a cap sized for a short answer is spent
    # before the answer begins, and the response stops with no text. On the hosted service the recall
    # gate (200) lost half its answers that way, and the reconcile judge (300) fails without a sign.
    # So every request asks for the answer's room and this much more.
    THINKING_ROOM = 2000

    def __init__(self, model: str) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic()
        self._model = model

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        import anthropic

        try:
            response = await self._create(system, user, max_tokens)
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise LLMRateLimited(str(exc)) from exc
        return self._text(response)

    async def _create(self, system: str, user: str, max_tokens: int):
        return await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens + self.THINKING_ROOM,
            system=system,
            messages=[{"role": "user", "content": user}],
            # Extraction is routine work: low effort keeps it fast and cheap.
            output_config={"effort": "low"},
        )

    @staticmethod
    def _text(response) -> str:
        if response.stop_reason == "refusal":
            raise LLMError(f"model refused: {getattr(response, 'stop_details', None)}")
        text = "".join(block.text for block in response.content if block.type == "text")
        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated at max_tokens" if text else
                           "response truncated at max_tokens before the answer began: thinking used the room")
        return text


class OpenAICompatibleLLM:
    """POST {base_url}/chat/completions, the request every OpenAI-compatible server accepts.
    Plain HTTP rather than a vendor SDK, so one backend serves them all. A local server needs
    no key, so the Authorization header is sent only when there is one."""

    def __init__(self, model: str, *, base_url: str, api_key: str = "", transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            # A local model can take minutes over a long conversation chunk.
            timeout=httpx.Timeout(600.0, connect=10.0),
            transport=transport,
        )
        # Most servers take `max_tokens`. OpenAI's reasoning models refuse it and ask for
        # `max_completion_tokens`; the first refusal switches this client over for good.
        self._limit_field = "max_tokens"

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        response = await self._post(system, user, max_tokens)
        if response.status_code == 400 and self._limit_field == "max_tokens" and "max_completion_tokens" in response.text:
            self._limit_field = "max_completion_tokens"
            response = await self._post(system, user, max_tokens)
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMRateLimited(f"{response.status_code} from the model server: {response.text[:300]}")
        if response.status_code != 200:
            raise LLMError(f"{response.status_code} from the model server: {response.text[:500]}")
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"the model server's reply has no choices: {response.text[:300]}") from exc
        if message.get("refusal"):
            raise LLMError(f"model refused: {message['refusal']}")
        if choice.get("finish_reason") == "length":
            raise LLMError("response truncated at max_tokens")
        return message.get("content") or ""

    async def _post(self, system: str, user: str, max_tokens: int) -> httpx.Response:
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self._limit_field: max_tokens,
        }
        try:
            return await self._client.post("chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach the model server: {exc!r}") from exc


class ClaudeCliLLM:
    """Runs the local `claude` binary in print mode with no tools, so a call is a
    plain completion. Serialized through a lock: parallel invocations compete for
    the same subscription rate limit and gain nothing."""

    def __init__(self, model: str) -> None:
        self._binary = shutil.which("claude")
        if not self._binary:
            raise LLMError("claude-cli backend selected but `claude` is not on PATH")
        self._model = model
        self._lock = asyncio.Semaphore(max(1, settings.claude_cli_concurrency))

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        args = [
            self._binary, "--print", "--no-session-persistence",
            "--output-format", "json", "--model", self._model,
            "--system-prompt", system, "--tools", "",
            # This machine may have the Memvine plugin installed: its hooks must not run on the
            # server's own extraction calls, or they would be sent back in as sessions.
            "--settings", '{"disableAllHooks": true}',
        ]
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                *args,
                env={**os.environ, "MEMVINE_HOOKS_DISABLED": "1"},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate(user.encode())
        if proc.returncode != 0:
            # With --output-format json the reason is in the JSON on stdout; stderr is often empty.
            detail = out.decode(errors="replace").strip()[-500:] or err.decode(errors="replace").strip()[-500:]
            if RATE_LIMIT_HINT.search(detail):
                raise LLMRateLimited(f"claude rate limited: {detail}")
            raise LLMError(f"claude exited {proc.returncode}: {detail or '(no output)'}")
        try:
            payload = json.loads(out.decode())
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude returned non-JSON output: {out[:200]!r}") from exc
        if payload.get("is_error") and RATE_LIMIT_HINT.search(str(payload.get("result"))):
            raise LLMRateLimited(f"claude rate limited: {payload.get('result')}")
        if payload.get("is_error"):
            raise LLMError(f"claude reported an error: {payload.get('result')}")
        return str(payload.get("result", ""))


@dataclass
class FakeLLM:
    """Returns the first canned response whose key appears in the user prompt.
    Records every call so tests can assert on what was asked."""

    responses: dict[str, str] = field(default_factory=dict)
    default: str = "[]"
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        for key, response in self.responses.items():
            if key in user:
                return response
        return self.default


def _openai_models() -> tuple[str, str]:
    """The main and fast model names for the openai backend. The defaults are Claude models,
    which another provider does not serve, so the main model has to be chosen; the fast model
    falls back to the main one."""
    defaults = Settings.model_fields
    model = settings.llm_model
    if model == defaults["llm_model"].default:
        raise LLMError("the openai backend needs MEMVINE_LLM_MODEL set to a model your provider serves")
    fast_model = settings.llm_model_fast
    if fast_model == defaults["llm_model_fast"].default:
        fast_model = model
    return model, fast_model


def make_llm(backend: str | None = None) -> LLM:
    """The main model, with `.fast` set to the fast model for high-volume simple calls."""
    backend = backend or settings.llm_backend
    if backend == "anthropic":
        llm, fast = AnthropicLLM(settings.llm_model), AnthropicLLM(settings.llm_model_fast)
    elif backend == "openai":
        model, fast_model = _openai_models()
        key = settings.llm_api_key or os.environ.get("OPENAI_API_KEY", "")
        llm = OpenAICompatibleLLM(model, base_url=settings.llm_base_url, api_key=key)
        fast = OpenAICompatibleLLM(fast_model, base_url=settings.llm_base_url, api_key=key)
    elif backend == "claude-cli":
        llm, fast = ClaudeCliLLM(settings.claude_cli_model), ClaudeCliLLM(settings.claude_cli_model_fast)
    elif backend == "fake":
        llm = FakeLLM()
        fast = llm  # tests see every call in one place
    else:
        raise LLMError(f"unknown llm backend: {backend}")
    llm.fast = fast
    return llm


def fast(llm: LLM) -> LLM:
    return getattr(llm, "fast", llm)
