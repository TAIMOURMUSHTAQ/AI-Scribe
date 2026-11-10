"""Exercises the real OpenAI code path against a simulated OpenAI server (no network, no key)."""
import asyncio
import json

import httpx
import pytest

from scribe.config import Settings
from scribe.pipeline import run_draft
from scribe.providers import OpenAIProvider, ProviderError, build_mock_draft
from scribe.schema import DraftRequest

NOTE = "/s/ 14/20 with min cues. Mom reports practice daily. Plan: continue."


def sse_body(text: str, finish="stop") -> bytes:
    chunks = [text[i:i + 40] for i in range(0, len(text), 40)]
    lines = []
    for c in chunks:
        lines.append({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                      "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]})
    lines.append({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                  "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    return ("".join(f"data: {json.dumps(l)}\n\n" for l in lines) + "data: [DONE]\n\n").encode()


def make(handler, model="gpt-4.1-mini"):
    cfg = Settings(provider="openai", openai_api_key="sk-test", clinical_model=model, max_retries=0)
    return OpenAIProvider(cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def collect(provider):
    return [ev async for ev in run_draft(DraftRequest(input=NOTE), provider)]


def test_streaming_structured_output_request_and_parse():
    seen = {}
    reply = json.dumps(build_mock_draft(NOTE, {}))

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body(reply))

    evs = asyncio.run(collect(make(handler)))
    assert seen["stream"] is True and seen["model"] == "gpt-4.1-mini"
    assert seen["response_format"]["type"] == "json_schema" and seen["response_format"]["json_schema"]["strict"] is True
    assert seen["temperature"] == 0.2 and "reasoning_effort" not in seen
    assert "<session_input>" in seen["messages"][1]["content"] and "70%" in seen["messages"][1]["content"]
    assert evs[-1]["event"] == "final"


def test_reasoning_model_omits_temperature():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse_body(json.dumps(build_mock_draft(NOTE, {}))))

    asyncio.run(collect(make(handler, model="gpt-5.4-mini")))
    assert "temperature" not in seen


@pytest.mark.parametrize("status,code", [(401, "auth"), (404, "model_not_found"), (429, "rate_limit")])
def test_errors_become_clear_messages(status, code):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "x", "type": "x"}})

    evs = asyncio.run(collect(make(handler)))
    assert evs[-1]["event"] == "error" and evs[-1]["data"]["code"] == code


def test_truncated_output_reported():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body('{"analysis": {"summ', "length"))

    evs = asyncio.run(collect(make(handler)))
    assert evs[-1]["event"] == "error" and evs[-1]["data"]["code"] == "too_long"


def test_missing_key_is_reported_not_hung():
    cfg = Settings(provider="openai", openai_api_key="")
    evs = asyncio.run(collect(OpenAIProvider(cfg)))
    assert evs[-1]["data"]["code"] == "no_key"


def test_transcription_sends_prompt():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ammi ne bataya 14 out of 20"})

    text = asyncio.run(make(handler).transcribe(b"abc", "p.webm", "audio/webm", "previous words"))
    assert text == "ammi ne bataya 14 out of 20"
    assert b"Urdu" in seen["body"] and b"previous words" in seen["body"]
