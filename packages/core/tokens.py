"""Cheap token estimation for context budgeting.

Budgeting runs on every candidate chunk of every request, so it must be local and fast.
~4 characters per token is a deliberate, slightly conservative approximation for English
text; exact counts come back from the model in ``usage`` and are recorded in the trace.
(For precise pre-flight counts, the Claude API offers messages.count_tokens - an extra
network round trip we don't want per chunk.)
"""

import math


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    if estimate_tokens(text) <= max_tokens:
        return text, False
    cut = max(0, max_tokens * 4 - 40)
    return text[:cut].rstrip() + "\n...[truncated to fit the context budget]", True
