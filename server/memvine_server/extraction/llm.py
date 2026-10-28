"""One interface over the model backends the synthesis pipeline can use.

Every call is "system prompt + user prompt in, text out". Callers parse JSON
out of the text themselves (see extract.parse_json), so a backend never has to
know what shape is expected.

Backends:
  AnthropicLLM  - the Anthropic SDK. Production.
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

from ..config import settings

RATE_LIMIT_HINT = re.compile(r"rate.?limit|usage limit|limit reached|too many requests|overloaded|429|529", re.IGNORECASE)


class LLM(Protocol):
    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str: ...


class LLMError(RuntimeError):
    pass


class LLMRateLimited(LLMError):
    """The provider asked us to slow down. The job is retried after a long pause, not counted
    against its attempts."""


class AnthropicLLM:
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
            max_tokens=max_tokens,
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
            raise LLMError("response truncated at max_tokens")
        return text


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


def make_llm(backend: str | None = None) -> LLM:
    """The main model, with `.fast` set to the fast model for high-volume simple calls."""
    backend = backend or settings.llm_backend
    if backend == "anthropic":
        llm, fast = AnthropicLLM(settings.llm_model), AnthropicLLM(settings.llm_model_fast)
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
