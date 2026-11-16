"""The openai backend: one request shape for every OpenAI-compatible server, and its failures
mapped onto the errors the worker already handles."""
import json

import httpx
import pytest

from memvine_server.config import Settings, settings
from memvine_server.extraction import llm as llm_mod
from memvine_server.extraction.llm import LLMError, LLMRateLimited, OpenAICompatibleLLM, make_llm


def reply(content="[]", finish="stop", **message):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content, **message}, "finish_reason": finish}]})


def backend(handler, **kwargs):
    seen = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    kwargs.setdefault("base_url", "http://model.test/v1")
    return OpenAICompatibleLLM("some-model", transport=httpx.MockTransport(record), **kwargs), seen


async def test_sends_one_chat_completion_and_returns_its_text():
    llm, seen = backend(lambda r: reply('[{"statement": "Retries cap at 3."}]'), api_key="sk-test")
    text = await llm.complete("Keep what stays true.", "Dana: the broker blocks a 4th retry.", max_tokens=900)
    assert text == '[{"statement": "Retries cap at 3."}]'
    (request,) = seen
    assert str(request.url) == "http://model.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {
        "model": "some-model",
        "messages": [
            {"role": "system", "content": "Keep what stays true."},
            {"role": "user", "content": "Dana: the broker blocks a 4th retry."},
        ],
        "max_tokens": 900,
    }


async def test_a_local_server_gets_no_authorization_header():
    llm, seen = backend(lambda r: reply(), base_url="http://localhost:11434/v1/")
    await llm.complete("s", "u")
    assert "authorization" not in seen[0].headers
    assert str(seen[0].url) == "http://localhost:11434/v1/chat/completions"


async def test_switches_to_max_completion_tokens_when_the_server_asks_for_it():
    def handler(request):
        if "max_tokens" in json.loads(request.content):
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        return reply("ok")

    llm, seen = backend(handler)
    assert await llm.complete("s", "u", max_tokens=50) == "ok"
    assert await llm.complete("s", "u", max_tokens=50) == "ok"
    bodies = [json.loads(r.content) for r in seen]
    assert [("max_tokens" in b, b.get("max_completion_tokens")) for b in bodies] == [(True, None), (False, 50), (False, 50)]


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_busy_server_is_a_rate_limit_so_the_job_waits(status):
    llm, _ = backend(lambda r: httpx.Response(status, text="slow down"))
    with pytest.raises(LLMRateLimited):
        await llm.complete("s", "u")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": "bad key"}}),
        httpx.Response(404, json={"error": {"message": "no such model"}}),
        httpx.Response(200, json={"error": "upstream failed"}),
        httpx.Response(200, text="<html>not json</html>"),
    ],
)
async def test_other_failures_are_errors_not_rate_limits(response):
    llm, _ = backend(lambda r: response)
    with pytest.raises(LLMError) as raised:
        await llm.complete("s", "u")
    assert not isinstance(raised.value, LLMRateLimited)


async def test_truncated_and_refused_replies_are_errors():
    llm, _ = backend(lambda r: reply("[{", finish="length"))
    with pytest.raises(LLMError, match="truncated"):
        await llm.complete("s", "u")
    llm, _ = backend(lambda r: reply(None, refusal="I can't help with that."))
    with pytest.raises(LLMError, match="refused"):
        await llm.complete("s", "u")


async def test_unreachable_server_is_an_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    llm, _ = backend(handler)
    with pytest.raises(LLMError, match="could not reach"):
        await llm.complete("s", "u")


async def test_empty_content_is_an_empty_string():
    llm, _ = backend(lambda r: reply(None))
    assert await llm.complete("s", "u") == ""


def test_make_llm_needs_a_model_chosen(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", Settings.model_fields["llm_model"].default)
    with pytest.raises(LLMError, match="MEMVINE_LLM_MODEL"):
        make_llm("openai")


def test_make_llm_uses_the_main_model_for_fast_calls_unless_one_is_set(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "big")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
    llm = make_llm("openai")
    assert (llm._model, llm_mod.fast(llm)._model) == ("big", "big")
    monkeypatch.setattr(settings, "llm_model_fast", "small")
    llm = make_llm("openai")
    assert (llm._model, llm_mod.fast(llm)._model) == ("big", "small")


def test_make_llm_reads_the_key_from_settings_then_openai_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "big")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert make_llm("openai")._client.headers["authorization"] == "Bearer sk-from-env"
    monkeypatch.setattr(settings, "llm_api_key", "sk-from-settings")
    assert make_llm("openai")._client.headers["authorization"] == "Bearer sk-from-settings"


def test_empty_model_variables_mean_the_defaults(monkeypatch):
    """Docker Compose passes an empty variable for each one .env leaves out."""
    for name in ("MEMVINE_LLM_BACKEND", "MEMVINE_LLM_MODEL", "MEMVINE_LLM_MODEL_FAST", "MEMVINE_LLM_BASE_URL"):
        monkeypatch.setenv(name, "")
    fresh = Settings(_env_file=None)
    defaults = Settings.model_fields
    assert fresh.llm_backend == "anthropic"
    assert fresh.llm_model == defaults["llm_model"].default
    assert fresh.llm_model_fast == defaults["llm_model_fast"].default
    assert fresh.llm_base_url == "https://api.openai.com/v1"
