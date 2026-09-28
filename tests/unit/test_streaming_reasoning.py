"""G3: streaming reasoning. The explanation streams as a dim line; display only."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from sable.agents import orchestrator, runtime
from sable.llm import anthropic, ollama, openai
from sable.llm.base import ExplanationStream, LLMResponse
from tests.fixtures.mock_llm import MockLLMBackend


def _feed_all(chunks):
    s = ExplanationStream()
    return [s.feed(c) for c in chunks][-1]


def _every_split(text):
    """The final extraction for every two-chunk split of text."""
    return {_feed_all([text[:i], text[i:]]) for i in range(len(text) + 1)}


class TestExplanationStream:
    def test_nothing_before_the_field(self):
        s = ExplanationStream()
        assert s.feed('{"action": "run", "command": "ls"') == ""

    def test_grows_as_chunks_arrive(self):
        s = ExplanationStream()
        assert s.feed('{"explanation": "List') == "List"
        assert s.feed(' files", "command"') == "List files"
        assert s.feed(': "ls"}') == "List files"

    def test_field_after_others(self):
        assert _feed_all(['{"command": "a \\"x\\"", ', '"explanation":"ok"}']) == "ok"

    def test_escapes(self):
        text = json.dumps({"explanation": 'say "hi"\nthen \\ and é'})
        assert _every_split(text) >= {'say "hi"\nthen \\ and é'}

    def test_chunk_boundary_inside_an_escape_never_shows_a_backslash(self):
        text = '{"explanation": "a\\"b\\u0041c"}'
        for i in range(len(text) + 1):
            partial = ExplanationStream().feed(text[:i])
            assert "\\" not in partial
            assert "a\"bAc".startswith(partial)
        assert _every_split(text) == {'a"bAc'}

    def test_never_appears(self):
        assert _feed_all(['{"action": "done", ', '"summary": "x"}']) == ""


# --- backends call on_text ---------------------------------------------------

_ANSWER = '{"action": "run", "command": "ls", "explanation": "list it"}'
_PIECES = [_ANSWER[:20], _ANSWER[20:40], _ANSWER[40:]]


def _run_backend(backend, body: str, content_type: str, monkeypatch):
    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    real = httpx.AsyncClient

    def fake_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    seen: list[str] = []
    resp = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "s", on_text=seen.append))
    return resp, seen


def _sse(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def test_openai_streams(monkeypatch):
    body = _sse([{"choices": [{"delta": {"content": p}}]} for p in _PIECES]) + "data: [DONE]\n\n"
    resp, seen = _run_backend(openai.OpenAIBackend("k", "gpt-4o-mini"), body, "text/event-stream", monkeypatch)
    assert seen == _PIECES
    assert resp.explanation == "list it" and resp.raw == _ANSWER


def test_anthropic_streams(monkeypatch):
    events = [{"type": "message_start", "message": {"usage": {"input_tokens": 1}}}]
    events += [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": p}} for p in _PIECES]
    resp, seen = _run_backend(anthropic.AnthropicBackend("k", "claude-x"), _sse(events), "text/event-stream", monkeypatch)
    assert seen == _PIECES
    assert resp.command == "ls"


def test_ollama_streams(monkeypatch):
    lines = [json.dumps({"message": {"content": p}}) for p in _PIECES]
    lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    resp, seen = _run_backend(ollama.OllamaBackend("http://o", "llama"), "\n".join(lines), "application/x-ndjson", monkeypatch)
    assert seen == _PIECES
    assert resp.explanation == "list it"


# --- call_llm ----------------------------------------------------------------

class _Old:
    """A backend written before G3: no on_text parameter."""

    async def complete(self, messages, system):
        return "old"


def test_call_llm_passes_on_text_through():
    seen: list[str] = []
    resp = runtime.call_llm(MockLLMBackend(), [{"role": "user", "content": "list files"}], "s", on_text=seen.append)
    assert resp.command == "ls -la"
    assert "".join(seen) and json.loads("".join(seen))["explanation"] == resp.explanation


def test_call_llm_without_on_text_keeps_old_backends_working():
    assert runtime.call_llm(_Old(), [], "s") == "old"


# --- orchestrator display ----------------------------------------------------

def test_spinner_gives_way_to_the_streamed_line(capsys):
    sp = orchestrator._Spinner()
    sp.start()
    for chunk in ['{"action":"run",', '"explanation":"check', ' disk"}']:
        sp.feed(chunk)
    assert sp._stop.is_set()  # spinner thread halted
    sp.stop()
    out = capsys.readouterr().out
    assert "\033[2m  check disk" in out
    assert out.endswith("\r\033[2K")  # cleared before the preview block


def test_spinner_stays_when_nothing_streams():
    sp = orchestrator._Spinner()
    sp.start()
    sp.feed('{"action":"done","summary":"x"}')
    assert not sp._stop.is_set()
    sp.stop()


def test_orchestrator_wires_the_spinner_into_call_llm(monkeypatch):
    got = {}

    def fake_call_llm(backend, messages, system, on_text=None):
        got["on_text"] = on_text
        return LLMResponse("", "", True, None, 0, 0, 0.0)

    monkeypatch.setattr(runtime, "call_llm", fake_call_llm)
    monkeypatch.setattr("sable.llm.registry.build_backend", lambda *a, **k: object())
    agent = orchestrator.OrchestratorAgent.__new__(orchestrator.OrchestratorAgent)
    agent._config = {}
    agent._spinner = orchestrator._Spinner()
    agent._call_llm([])
    assert got["on_text"] == agent._spinner.feed
