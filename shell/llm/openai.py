"""OpenAI LLM backend.

Uses httpx with httpx-sse for streaming. Timeout is always 30 seconds.
Token counts extracted from the final SSE chunk. Cost calculated locally
from llm/pricing.json.
"""
from __future__ import annotations

import json
import httpx
from httpx_sse import aconnect_sse

from shell.llm.base import LLMBackend, LLMResponse, parse_llm_json, calculate_cost

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIBackend(LLMBackend):
    """OpenAI backend — connects to the OpenAI API."""

    def __init__(self, api_key: str, model: str) -> None:
        """
        Args:
            api_key: OpenAI API key.
            model: Model name, e.g. 'gpt-4o'.
        """
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: list[dict], system: str) -> LLMResponse:
        """Send a completion request to OpenAI with SSE streaming.

        Always sets timeout=httpx.Timeout(30.0).

        Args:
            messages: Conversation history.
            system: System prompt.

        Returns:
            Parsed LLMResponse with cost calculated from pricing table.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with aconnect_sse(
                client, "POST", OPENAI_API_URL, json=payload, headers=headers
            ) as event_source:
                async for event in event_source.aiter_sse():
                    if event.data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(event.data)
                    except json.JSONDecodeError:
                        continue

                    # Accumulate text from delta
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            full_text += content

                    # Usage comes in the final chunk when stream_options.include_usage=True
                    usage = chunk.get("usage")
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        completion_tokens = usage.get("completion_tokens", 0)

        # Parse JSON from model response — fallback chain
        try:
            parsed = parse_llm_json(full_text)
        except ValueError:
            # Re-ask model (non-streaming) to extract JSON
            retry_payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                ] + messages + [
                    {"role": "assistant", "content": full_text},
                    {
                        "role": "user",
                        "content": "Extract only the JSON object from your previous response. Return ONLY valid JSON, no markdown.",
                    },
                ],
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.post(OPENAI_API_URL, json=retry_payload, headers=headers)
                resp.raise_for_status()
                retry_data = resp.json()
                retry_text = retry_data["choices"][0]["message"]["content"]
                usage2 = retry_data.get("usage", {})
                prompt_tokens += usage2.get("prompt_tokens", 0)
                completion_tokens += usage2.get("completion_tokens", 0)
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
            model=self.model,
            done=bool(parsed.get("done", False)),
            spawn=parsed.get("spawn") or None,
        )
