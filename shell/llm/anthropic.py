"""Anthropic Claude LLM backend.

Uses httpx with httpx-sse for streaming. Timeout is always 30 seconds.
MUST include the 'anthropic-version: 2023-06-01' header — requests fail without it.
Token counts extracted from the final SSE chunk. Cost calculated locally
from llm/pricing.json.
"""
from __future__ import annotations

import json
import httpx
from httpx_sse import aconnect_sse

from shell.llm.base import LLMBackend, LLMResponse, parse_llm_json, calculate_cost

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION_HEADER = "2023-06-01"


class AnthropicBackend(LLMBackend):
    """Anthropic backend — connects to the Anthropic API."""

    def __init__(self, api_key: str, model: str) -> None:
        """
        Args:
            api_key: Anthropic API key.
            model: Model name, e.g. 'claude-3-5-haiku-20241022'.
        """
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: list[dict], system: str) -> LLMResponse:
        """Send a completion request to Anthropic with SSE streaming.

        Always includes 'anthropic-version: 2023-06-01' header.
        Always sets timeout=httpx.Timeout(30.0).

        Args:
            messages: Conversation history.
            system: System prompt.

        Returns:
            Parsed LLMResponse with cost calculated from pricing table.
        """
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION_HEADER,
            "content-type": "application/json",
        }

        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with aconnect_sse(
                client, "POST", ANTHROPIC_API_URL, json=payload, headers=headers
            ) as event_source:
                async for event in event_source.aiter_sse():
                    try:
                        data = json.loads(event.data)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            full_text += delta.get("text", "")

                    elif event_type == "message_delta":
                        usage = data.get("usage", {})
                        completion_tokens = usage.get("output_tokens", completion_tokens)

                    elif event_type == "message_start":
                        usage = data.get("message", {}).get("usage", {})
                        prompt_tokens = usage.get("input_tokens", 0)

        # Parse JSON from model response — fallback chain
        try:
            parsed = parse_llm_json(full_text)
        except ValueError:
            # Re-ask model (non-streaming) to extract JSON
            retry_payload = {
                "model": self.model,
                "max_tokens": 512,
                "system": system,
                "messages": messages + [
                    {"role": "assistant", "content": full_text},
                    {
                        "role": "user",
                        "content": "Extract only the JSON object from your previous response. Return ONLY valid JSON, no markdown.",
                    },
                ],
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.post(ANTHROPIC_API_URL, json=retry_payload, headers=headers)
                resp.raise_for_status()
                retry_data = resp.json()
                retry_text = retry_data["content"][0]["text"]
                usage2 = retry_data.get("usage", {})
                prompt_tokens += usage2.get("input_tokens", 0)
                completion_tokens += usage2.get("output_tokens", 0)
            try:
                parsed = parse_llm_json(retry_text)
            except ValueError:
                raise ValueError(f"LLM returned unparseable response: {full_text[:300]}")

        cost = calculate_cost(prompt_tokens, completion_tokens, self.model)

        return LLMResponse(
            command=parsed.get("command", ""),
            explanation=parsed.get("explanation", ""),
            safe=parsed.get("safe", True),
            plan=parsed.get("plan"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            done=bool(parsed.get("done", False)),
        )
