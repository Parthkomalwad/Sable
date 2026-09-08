"""Input router classifies each line as bash or agentic (NL).

Two modes:
- prefix mode: lines starting with '>>' route to LLM; everything else to bash.
- auto-detect mode: heuristic scoring on bash signals vs NL signals.

This module must have no side effects and be independently unit-testable.
"""
from __future__ import annotations
from enum import Enum


class Route(Enum):
    BASH = "bash"
    AGENTIC = "agentic"
    AMBIGUOUS = "ambiguous"


import shutil
import re

SHELL_SYNTAX = re.compile(r'[|><`]|\$\(|\&\&|\|\||\;|(?<!\w)--?[a-zA-Z]')
QUESTION_WORDS = {'what', 'how', 'why', 'where', 'when', 'which'}
ARTICLES = {'the', 'a', 'an'}
INSTRUCTIONAL_PHRASES = ['show me', 'find all', 'list all', 'give me', 'check if', 'how do', 'can you']

# Shell builtins that are never in PATH but must always route to bash
SHELL_BUILTINS = {
    'cd', 'export', 'source', 'alias', 'unalias', 'exit', 'eval',
    'set', 'unset', 'exec', 'type', 'read', 'echo', 'printf',
    'pushd', 'popd', 'dirs', 'jobs', 'fg', 'bg', 'wait', 'kill',
    'history', 'fc', 'umask', 'ulimit', 'true', 'false',
}


def classify(line: str, mode: str = "auto") -> Route:
    """Classify a line of input as BASH, AGENTIC, or AMBIGUOUS.

    Args:
        line: Raw user input string.
        mode: "prefix" or "auto".

    Returns:
        Route enum value.
    """
    stripped = line.strip()
    if not stripped:
        return Route.BASH  # empty → bash is safe default

    # Prefix mode
    if mode == "prefix":
        if stripped.startswith(">>"):
            return Route.AGENTIC
        return Route.BASH

    # Auto mode
    bash_score = 0
    nl_score = 0

    words = stripped.lower().split()
    first_word = words[0] if words else ""

    # Shell builtins always route to bash immediately
    if first_word in SHELL_BUILTINS:
        return Route.BASH

    # Bash signals
    if shutil.which(first_word):
        bash_score += 1
    if SHELL_SYNTAX.search(stripped):
        bash_score += 2  # weight higher strong signal

    # NL signals
    word_set = set(words)
    if word_set & QUESTION_WORDS:
        nl_score += 2
    if word_set & ARTICLES:
        nl_score += 1
    for phrase in INSTRUCTIONAL_PHRASES:
        if phrase in stripped.lower():
            nl_score += 2
    if not SHELL_SYNTAX.search(stripped):
        nl_score += 1
    if not shutil.which(first_word):
        nl_score += 1
    # Score rest of words as English prose (beyond first word)
    rest_words = set(words[1:]) if len(words) > 1 else set()
    if rest_words & QUESTION_WORDS:
        nl_score += 1
    if rest_words & ARTICLES:
        nl_score += 1

    if nl_score > bash_score:
        return Route.AGENTIC
    if bash_score > nl_score:
        return Route.BASH
    # Tie: if first word is a known command, prefer bash over asking
    if shutil.which(first_word):
        return Route.BASH
    return Route.AMBIGUOUS
