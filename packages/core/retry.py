"""Retry with exponential backoff + jitter, for *transient* failures only.

Retrying a 400/401/403/404 just repeats a mistake (and can double-apply a write), so
callers classify errors: only exceptions for which ``is_retryable(exc)`` is true are
retried. After ``attempts`` tries the last error is raised with the attempt count
attached, so callers can report "unavailable after 3 attempts" instead of a stack trace.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class RetryExhaustedError(Exception):
    def __init__(self, last_error: Exception, attempts: int):
        super().__init__(f"{last_error} (after {attempts} attempts)")
        self.last_error = last_error
        self.attempts = attempts


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    attempts: int = 3,
    base_delay: float = 0.25,
    max_delay: float = 2.0,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            last = exc
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.8 + random.random() * 0.4)  # noqa: S311
            if on_retry:
                on_retry(attempt, exc)
            log.warning(
                "transient failure, retrying",
                extra={"attempt": attempt, "delay_s": round(delay, 3), "error": str(exc)[:200]},
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise RetryExhaustedError(last, attempts)
