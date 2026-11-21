"""Streaming calls that drop mid-response are retried, not fatal.

The deployment talks to a gateway that resets long streams now and then
(``upstream closed early: Connection reset by peer``), which used to fail the
whole analysis. These tests pin the behaviour at the public ``stream`` seam:
a dropped stream is retried, and the partial text from the failed attempt is
discarded so the caller never sees duplicated or truncated output.
"""

import httpx
import openai
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI


def _connection_error():
    return openai.APIConnectionError(
        message="Connection error.",
        request=httpx.Request("POST", "http://gateway.test/v1/chat/completions"),
    )


def _chunk(text: str) -> ChatGenerationChunk:
    return ChatGenerationChunk(message=AIMessageChunk(content=text))


@pytest.fixture
def llm():
    return NormalizedChatOpenAI(model="test-model", api_key="test-key", base_url="http://gateway.test/v1")


def test_stream_retries_after_connection_error(monkeypatch, llm):
    monkeypatch.setenv("TRADINGAGENTS_STREAM_RETRIES", "2")
    attempts = {"count": 0}

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        attempts["count"] += 1
        first = attempts["count"] == 1

        def gen():
            yield _chunk("hel")
            if first:
                raise _connection_error()
            yield _chunk("lo")

        return gen()

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)

    text = "".join(chunk.content for chunk in llm.stream([HumanMessage(content="hi")]))

    assert attempts["count"] == 2
    assert text == "hello"


def test_stream_retries_after_mid_stream_reset(monkeypatch, llm):
    monkeypatch.setenv("TRADINGAGENTS_STREAM_RETRIES", "3")
    attempts = {"count": 0}

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        attempts["count"] += 1
        attempt = attempts["count"]

        def gen():
            yield _chunk("par")
            if attempt == 1:
                raise openai.APIError(
                    message="upstream closed early: [Errno 104] Connection reset by peer",
                    request=httpx.Request("POST", "http://gateway.test/v1/chat/completions"),
                    body=None,
                )
            yield _chunk("tial")

        return gen()

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)

    text = "".join(chunk.content for chunk in llm.stream([HumanMessage(content="hi")]))

    assert attempts["count"] == 2
    assert text == "partial"


def test_stream_raises_when_retries_are_exhausted(monkeypatch, llm):
    monkeypatch.setenv("TRADINGAGENTS_STREAM_RETRIES", "2")
    attempts = {"count": 0}

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        attempts["count"] += 1

        def gen():
            yield _chunk("x")
            raise _connection_error()

        return gen()

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)

    with pytest.raises(openai.APIConnectionError):
        list(llm.stream([HumanMessage(content="hi")]))
    assert attempts["count"] == 2


def test_stream_does_not_retry_when_disabled(monkeypatch, llm):
    monkeypatch.delenv("TRADINGAGENTS_STREAM_RETRIES", raising=False)
    attempts = {"count": 0}

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        attempts["count"] += 1

        def gen():
            raise _connection_error()
            yield  # pragma: no cover

        return gen()

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)

    with pytest.raises(openai.APIConnectionError):
        list(llm.stream([HumanMessage(content="hi")]))
    assert attempts["count"] == 1


def test_stream_does_not_retry_unrelated_errors(monkeypatch, llm):
    monkeypatch.setenv("TRADINGAGENTS_STREAM_RETRIES", "3")
    attempts = {"count": 0}

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        attempts["count"] += 1

        def gen():
            raise ValueError("bad payload")
            yield  # pragma: no cover

        return gen()

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)

    with pytest.raises(ValueError):
        list(llm.stream([HumanMessage(content="hi")]))
    assert attempts["count"] == 1