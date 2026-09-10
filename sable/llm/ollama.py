"""Ollama local LLM backend.

Uses httpx for NDJSON streaming (newline-delimited JSON). Timeout is always 30 seconds.
Token counts are extracted from the final chunk's prompt_eval_count / eval_count fields.
Cost is always $0.00 for local models.
"""
from __future__ import annotations
import json
import httpx
from sable.llm.base import LLMBackend, LLMResponse, parse_llm_json, calculate_cost


class OllamaBackend(LLMBackend):
    """Ollama backend connects to a local Ollama instance via HTTP."""

    def __init__(self, base_url: str, model: str) -> None:
        """
        Args:
            base_url: Ollama server URL, e.g. 'http://localhost:11434'.
            model: Model name, e.g. 'llama3.1'.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(self, messages: list[dict], system: str) -> LLMResponse:
        """Send a completion request to Ollama with NDJSON streaming.

        Always sets timeout=httpx.Timeout(30.0).

        Args:
            messages: Conversation history.
            system: System prompt.

        Returns:
            Parsed LLMResponse with cost_usd=0.0.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": True,
        }

        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if "message" in chunk and "content" in chunk["message"]:
                        full_text += chunk["message"]["content"]

                    if chunk.get("done"):
                        prompt_tokens = chunk.get("prompt_eval_count", 0)
                        completion_tokens = chunk.get("eval_count", 0)

        # Parse JSON from model response fallback chain steps 1+2
        try:
            parsed = parse_llm_json(full_text)
        except ValueError:
            # Fallback step 3: re-ask model to extract JSON
            retry_messages = messages + [
                {"role": "assistant", "content": full_text},
                {
                    "role": "user",
                    "content": "Extract only the JSON object from your previous response. Return ONLY valid JSON, no markdown.",
                },
            ]
            retry_payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}] + retry_messages,
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat", json=retry_payload
                )
                resp.raise_for_status()
                retry_text = resp.json().get("message", {}).get("content", "")
            try:
                parsed = parse_llm_json(retry_text)
            except ValueError:
                # Fallback step 4: raise so caller can show raw text to user
                raise ValueError(
                    f"LLM returned unparseable response: {full_text[:300]}"
                )

        cost = calculate_cost(prompt_tokens, completion_tokens, f"ollama/{self.model}")

        return LLMResponse(
            command=parsed.get("command", ""),
            explanation=parsed.get("explanation", ""),
            safe=parsed.get("safe", True),
            plan=parsed.get("plan"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            done=bool(parsed.get("done", False)),
            spawn=parsed.get("spawn") or None,
            action=parsed.get("action", ""),
        )
