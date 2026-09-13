"""Backends preserve what the model actually said.

`LLMResponse` carries eleven typed fields shaped around the
`{command, explanation, safe, plan}` contract. Every backend builds one
with `parsed.get(...)` on exactly those names, so a model answering a
question *outside* that schema had its entire answer discarded: the caller
received a well-formed, completely empty response, with no error and no way
to tell "the model said nothing" from "the model said something we dropped".

Found by a live run, not by a test. `gpt-4o-mini` answered the B3
crystallisation prompt with correct JSON (`reusable`, `name`, `triggers`,
`validate`, `body`), 166 completion tokens were billed, and
`SkillCrystalliser.from_run()` drafted nothing at all. Every one of its 18
unit tests passed throughout, because each stubbed a backend returning
`explanation=<the JSON>`: they modelled the implementation rather than the
transport, so none of them crossed the seam where the loss happened.

These tests drive the **real backend objects** against canned HTTP
responses. That is the whole point: a stubbed backend returning a
hand-built `LLMResponse` can never catch a bug in how a backend builds one.
"""
from __future__ import annotations

import json

import httpx
import pytest

from sable.llm.anthropic import AnthropicBackend
from sable.llm.base import LLMResponse
from sable.llm.ollama import OllamaBackend
from sable.llm.openai import OpenAIBackend

#: A real answer to `llm/prompts/crystallise_check.md`. None of its keys is
#: in the base schema, which is exactly why it used to vanish.
NON_SCHEMA_ANSWER = json.dumps({
    "reusable": True,
    "name": "deploy-api",
    "description": "Builds and deploys the API using Docker Compose.",
    "triggers": ["deploy the api"],
    "validate": "curl -sf localhost:8080/health",
    "body": "# Deploy the API\n\n1. Build.\n2. Start.\n",
})


def _sse(chunks: list[dict]) -> bytes:
    """An OpenAI-style SSE stream."""
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _openai_stream(text: str) -> bytes:
    chunks = [{"choices": [{"delta": {"content": piece}}]} for piece in (text,)]
    chunks.append({
        "choices": [],
        "usage": {"prompt_tokens": 480, "completion_tokens": 166},
    })
    return _sse(chunks)


def _anthropic_stream(text: str) -> bytes:
    """Anthropic's SSE shape, as `anthropic.py` actually reads it.

    It dispatches on `data["type"]` inside the JSON payload rather than on
    the SSE `event:` line, and only accumulates a delta whose own `type` is
    `text_delta`. An event missing either is skipped silently, which is how
    a first attempt at this fixture produced an empty `full_text` and looked
    like a backend bug.
    """
    events = [
        "event: message_start\n"
        f"data: {json.dumps({'type': 'message_start', 'message': {'usage': {'input_tokens': 480}}})}\n\n",
        "event: content_block_delta\n"
        f"data: {json.dumps({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}})}\n\n",
        "event: message_delta\n"
        f"data: {json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 166}})}\n\n",
    ]
    return "".join(events).encode()


@pytest.fixture
def transport_factory(monkeypatch):
    """Point a backend's httpx client at a canned response."""

    def _install(body: bytes, *, json_body: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            if json_body is not None:
                return httpx.Response(200, json=json_body)
            return httpx.Response(
                200, content=body,
                headers={"Content-Type": "text/event-stream"},
            )

        transport = httpx.MockTransport(handler)
        real_init = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = transport
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    return _install


class TestTheRawTextSurvives:
    """The field that stops a non-schema answer being thrown away."""

    def test_openai_keeps_the_raw_text(self, transport_factory):
        import asyncio

        transport_factory(_openai_stream(NON_SCHEMA_ANSWER))
        backend = OpenAIBackend(api_key="sk-test", model="gpt-4o-mini")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))

        assert response.raw == NON_SCHEMA_ANSWER

    def test_anthropic_keeps_the_raw_text(self, transport_factory):
        import asyncio

        transport_factory(_anthropic_stream(NON_SCHEMA_ANSWER))
        backend = AnthropicBackend(api_key="sk-ant-test", model="claude-sonnet-5")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))

        assert response.raw == NON_SCHEMA_ANSWER

    def test_ollama_keeps_the_raw_text(self, transport_factory):
        import asyncio

        body = (json.dumps({"message": {"content": NON_SCHEMA_ANSWER}, "done": True})
                + "\n").encode()
        transport_factory(body)
        backend = OllamaBackend(base_url="http://localhost:11434", model="llama3.1")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))

        assert response.raw == NON_SCHEMA_ANSWER


class TestTheNonSchemaAnswerIsUsable:
    """The bug in the terms the caller experienced it.

    B3 reads the model's answer and drafts a skill from it. Before the raw
    field, this is where 166 billed tokens became nothing.
    """

    def test_the_crystallise_answer_can_be_parsed_back(self, transport_factory):
        import asyncio

        from sable.agents import runtime

        transport_factory(_openai_stream(NON_SCHEMA_ANSWER))
        backend = OpenAIBackend(api_key="sk-test", model="gpt-4o-mini")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))
        parsed = runtime.parse_json_action(response.raw, {})

        assert parsed.get("reusable") is True
        assert parsed.get("name") == "deploy-api"
        assert parsed.get("body")

    def test_the_typed_fields_are_still_empty_for_a_non_schema_answer(
        self, transport_factory
    ):
        """`raw` is an addition, not a change to what the typed fields mean.

        An answer carrying no `command` still reports no command. Anything
        else would have every non-schema answer look like a command to run.
        """
        import asyncio

        transport_factory(_openai_stream(NON_SCHEMA_ANSWER))
        backend = OpenAIBackend(api_key="sk-test", model="gpt-4o-mini")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))

        assert response.command == ""
        assert response.explanation == ""


class TestOrdinarySchemaAnswersAreUnaffected:
    """Additive: every existing caller keeps reading the typed fields."""

    def test_a_schema_answer_still_populates_command_and_explanation(
        self, transport_factory
    ):
        import asyncio

        schema = json.dumps({
            "command": "ls -la", "explanation": "list files",
            "safe": True, "plan": None,
        })
        transport_factory(_openai_stream(schema))
        backend = OpenAIBackend(api_key="sk-test", model="gpt-4o-mini")

        response = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "sys"))

        assert response.command == "ls -la"
        assert response.explanation == "list files"
        assert response.raw == schema


class TestTheFieldDefaults:
    def test_raw_defaults_to_empty_so_existing_constructions_work(self):
        """Every fixture and test builds an LLMResponse without `raw`.

        A required field would break all of them at once, which is a bad
        trade for a field whose whole purpose is to stop data being lost.
        """
        response = LLMResponse(
            command="ls", explanation="e", safe=True, plan=None,
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0,
        )
        assert response.raw == ""
