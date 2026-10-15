"""Per-tenant fixed-window rate limiting in Redis.

One noisy tenant must not exhaust shared LLM quota or starve other tenants. A fixed
window (INCR + EXPIRE in one pipeline) is simple, atomic, and shared across all API
replicas because the counter lives in Redis rather than process memory.
"""

import time
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


async def check_rate_limit(redis: Redis, key: str, limit: int, window_s: int = 60) -> RateLimitResult:
    window = int(time.time()) // window_s
    redis_key = f"tp:ratelimit:{key}:{window}"
    pipe = redis.pipeline(transaction=True)
    pipe.incr(redis_key)
    pipe.expire(redis_key, window_s + 5)
    count, _ = await pipe.execute()
    retry_after = window_s - int(time.time()) % window_s
    return RateLimitResult(
        allowed=count <= limit, limit=limit, remaining=max(0, limit - count), retry_after_s=retry_after
    )
