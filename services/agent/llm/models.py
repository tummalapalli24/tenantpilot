"""Model catalog + capability table, and provider selection.

Tenant config is *model-aware*: newer Claude models reject sampling parameters
(``temperature`` returns HTTP 400 on Opus 5 / Sonnet 5), ``effort`` only exists on some
models, and structured outputs are model-gated. Rather than letting a tenant's config
break at request time, the provider consults this table and records what it had to
drop in the trace ("temperature not sent: ...").
"""

from dataclasses import dataclass
from functools import lru_cache

from packages.core.config import get_settings
from services.agent.llm.base import LLMError, LLMProvider

OFFLINE_MODEL = "offline-heuristic"


@dataclass(frozen=True)
class Capabilities:
    sampling: bool = False  # accepts temperature/top_p
    effort: bool = False  # accepts output_config.effort
    structured_output: bool = False  # accepts output_config.format (JSON schema)
    adaptive_thinking: bool = False  # thinking={"type": "adaptive"}
    refusal_fallbacks: bool = False  # server-side fallbacks="default" (beta)


CAPABILITIES: dict[str, Capabilities] = {
    "claude-opus-5": Capabilities(effort=True, structured_output=True, adaptive_thinking=True, refusal_fallbacks=True),
    "claude-sonnet-5": Capabilities(effort=True, structured_output=True, adaptive_thinking=True),
    "claude-opus-4-8": Capabilities(effort=True, structured_output=True, adaptive_thinking=True),
    "claude-haiku-4-5": Capabilities(sampling=True, structured_output=True),
}

SELECTABLE_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", OFFLINE_MODEL]


def capabilities(model: str) -> Capabilities:
    # Unknown Claude models get the most conservative request shape.
    return CAPABILITIES.get(model, Capabilities())


@lru_cache
def _anthropic_provider():
    from services.agent.llm.anthropic_provider import AnthropicProvider

    settings = get_settings()
    return AnthropicProvider(
        api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries
    )


def get_provider(model: str) -> LLMProvider:
    if model == OFFLINE_MODEL:
        from services.agent.llm.offline_provider import OfflineHeuristicProvider

        return OfflineHeuristicProvider()
    if model.startswith("claude-"):
        if not get_settings().anthropic_api_key:
            raise LLMError(
                f"Model {model} needs ANTHROPIC_API_KEY, which is not configured. Set it, or switch this "
                f"tenant to '{OFFLINE_MODEL}'.",
                kind="not_configured",
            )
        return _anthropic_provider()
    raise LLMError(f"Unknown model '{model}'", kind="bad_request")
