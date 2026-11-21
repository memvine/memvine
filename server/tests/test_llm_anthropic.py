"""The anthropic backend: a caller's max_tokens is the room its answer needs, and the model's
thinking, which counts toward the same limit, gets room of its own."""
from types import SimpleNamespace

import pytest

from memvine_server.extraction.llm import AnthropicLLM, LLMError


def thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def text(value):
    return SimpleNamespace(type="text", text=value)


def backend(monkeypatch, response):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-key")  # the client is replaced: nothing is sent
    llm, asked = AnthropicLLM("claude-haiku-5-5"), []

    async def create(**request):
        asked.append(request)
        return response

    llm._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return llm, asked


async def test_a_short_answer_still_leaves_the_model_room_to_think(monkeypatch):
    """The recall gate asks for 200 tokens, enough for its answer. Sent as the request's whole limit,
    the model's thinking used it up and half the gate's answers on the hosted service never began."""
    llm, asked = backend(monkeypatch, SimpleNamespace(stop_reason="end_turn", content=[thinking(), text('{"use": [2]}')]))
    assert await llm.complete("Choose.", "1. a\n2. b", max_tokens=200) == '{"use": [2]}'
    assert asked[0]["max_tokens"] == 200 + AnthropicLLM.THINKING_ROOM
    assert asked[0]["model"] == "claude-haiku-5-5" and asked[0]["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("content, message", [
    ([thinking()], "before the answer began: thinking used the room"),
    ([thinking(), text('{"use": [1')], "response truncated at max_tokens$"),
])
async def test_a_cut_off_response_is_an_error_that_says_where_it_stopped(monkeypatch, content, message):
    llm, _ = backend(monkeypatch, SimpleNamespace(stop_reason="max_tokens", content=content))
    with pytest.raises(LLMError, match=message):
        await llm.complete("Choose.", "1. a", max_tokens=200)
