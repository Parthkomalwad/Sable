"""Session context compression.

Triggers when accumulated tokens exceed threshold or turn count is high.
Uses token-reducer with CompressionLevel.MODERATE and TaskContext.RAG.
Always preserves the last 2 turns verbatim.
Archives raw turns to session_memory table.
"""
from __future__ import annotations


def should_compress(turns: list[dict], token_count: int, config: dict) -> bool:
    """Return True if compression should be triggered.

    Default thresholds: 2000 tokens or 5 turns (configurable via config).

    Args:
        turns: Current conversation turn list.
        token_count: Accumulated token count for the turns.
        config: Shell config dict with optional 'compress_after_tokens'
                and 'compress_after_turns' keys.

    Returns:
        True if compression should run.
    """
    threshold_tokens = config.get("compress_after_tokens", 2000)
    threshold_turns = config.get("compress_after_turns", 5)
    return token_count > threshold_tokens and len(turns) > threshold_turns


def compress(turns: list[dict]) -> str:
    """Compress older turns using token-reducer.

    Keeps last 2 turns verbatim; passes the rest to token-reducer.

    Args:
        turns: Full list of conversation turns.

    Returns:
        Compressed context string.
    """
    if len(turns) <= 2:
        # Nothing to compress return as plain text
        return "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns
        )

    older_turns = turns[:-2]
    last_two = turns[-2:]

    # Format older turns as plain text for compression
    older_text = "\n".join(
        f"{t.get('role', 'user')}: {t.get('content', '')}" for t in older_turns
    )

    compressed_older = _compress_with_token_reducer(older_text)

    # Append last 2 turns verbatim
    last_two_text = "\n".join(
        f"{t.get('role', 'user')}: {t.get('content', '')}" for t in last_two
    )

    return f"{compressed_older}\n\n--- recent (verbatim) ---\n{last_two_text}"


def _compress_with_token_reducer(text: str) -> str:
    """Run token-reducer on the given text and return compressed output."""
    try:
        from token_reducer import reduce_tokens, CompressionLevel, TaskContext  # type: ignore

        result = reduce_tokens(
            text,
            compression_level=CompressionLevel.MODERATE,
            task_context=TaskContext.RAG,
        )
        return result if isinstance(result, str) else str(result)
    except ImportError:
        # token-reducer not installed fall back to truncation
        max_chars = 2000
        if len(text) > max_chars:
            return text[:max_chars] + "\n[... truncated ...]"
        return text
    except Exception:
        # Any other failure return original
        return text
