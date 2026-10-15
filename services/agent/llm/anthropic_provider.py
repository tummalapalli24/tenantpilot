"""Claude via the official Anthropic Python SDK (1.x).

Request shaping is driven by the capability table (services/agent/llm/models.py):

* ``temperature`` - SDK 1.x removed it from the typed signature; models that still
  accept it (e.g. Haiku 4.5) get it through ``extra_body``. Opus 5 / Sonnet 5 reject it
  with a 400, so it is dropped and the drop is reported in ``notes`` (visible in traces).
* ``output_config.effort`` - per-tenant reasoning effort, where supported.
* ``output_config.format`` - the final answer is constrained to a JSON schema.
* ``thinking`` - adaptive with a summarized display, so the trace can show a reasoning
  summary. Thinking blocks are echoed back unchanged on the next turn.
* ``fallbacks="default"`` (beta) - on Opus 5, a safety-classifier decline is re-run
  server-side on the recommended fallback model instead of failing the run.
* top-level ``cache_control`` - automatic prompt caching; each agent-loop iteration
  re-sends the same tools + system + growing transcript, so later calls hit the cache.

The SDK already retries 408/409/429/5xx and connection errors (``max_retries``); here we
only translate the typed exceptions into provider-neutral LLMErrors.
"""

from typing import Any

import anthropic

from services.agent.llm.base import LLMError, LLMResponse, LLMUsage, ModelConfig, ToolSpec
from services.agent.llm.models import capabilities

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def sanitize_for_echo(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks before a mid-output ``fallback`` marker (and the marker itself) must not be
    echoed back as history; everything after it is echoed normally."""
    last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return content
    dropped_before = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    head = [b for b in content[:last] if b.get("type") not in dropped_before]
    return head + content[last + 1 :]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, timeout: float = 120.0, max_retries: int = 3):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    def build_request(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        config: ModelConfig,
        output_schema: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[str], bool]:
        caps = capabilities(config.model)
        notes: list[str] = []
        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "system": [{"type": "text", "text": system}],
            "messages": messages,
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = [t.to_api() for t in tools]
        extra_body: dict[str, Any] = {}
        if caps.sampling:
            extra_body["temperature"] = config.temperature
        else:
            notes.append(f"temperature={config.temperature} not sent: {config.model} does not accept sampling params")
        output_config: dict[str, Any] = {}
        if caps.effort:
            output_config["effort"] = config.effort
        if output_schema is not None:
            if caps.structured_output:
                output_config["format"] = {"type": "json_schema", "schema": output_schema}
            else:
                notes.append("structured outputs unsupported for this model; relying on prompt + validation")
        if output_config:
            kwargs["output_config"] = output_config
        if caps.adaptive_thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs, notes, caps.refusal_fallbacks

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        config: ModelConfig,
        output_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs, notes, use_fallbacks = self.build_request(
            system=system, messages=messages, tools=tools, config=config, output_schema=output_schema
        )
        try:
            if use_fallbacks:
                message = await self.client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                message = await self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key", kind="auth") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("API key lacks permission for this model", kind="auth") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Model {config.model} not found", kind="bad_request") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(
                "Rate limited by the LLM provider (after SDK retries)", kind="rate_limited", retryable=True
            ) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"LLM request rejected: {exc.message}", kind="bad_request") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"LLM provider error HTTP {exc.status_code}", kind="unavailable", retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM provider", kind="unavailable", retryable=True) from exc

        content = sanitize_for_echo([block.model_dump(mode="json", exclude_none=True) for block in message.content])
        usage = message.usage
        refusal_category = None
        if message.stop_reason == "refusal" and getattr(message, "stop_details", None):
            refusal_category = getattr(message.stop_details, "category", None)
        if message.model != config.model:
            notes.append(f"served by fallback model {message.model}")
        return LLMResponse(
            content=content,
            stop_reason=message.stop_reason or "end_turn",
            usage=LLMUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            model=message.model,
            request_id=getattr(message, "_request_id", None),
            notes=notes,
            refusal_category=refusal_category,
        )
