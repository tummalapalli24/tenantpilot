"""Provider-neutral LLM interface used by the agent loop.

Messages use the Anthropic content-block shape (text / tool_use / tool_result / thinking
blocks as plain JSON dicts). That shape is also what we persist in ``agent_runs.state``,
so a paused run can be resumed later - by any provider implementation - byte-for-byte.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

LLMErrorKind = Literal["not_configured", "auth", "rate_limited", "unavailable", "bad_request", "refused"]


class LLMError(Exception):
    def __init__(self, message: str, *, kind: LLMErrorKind, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool

    def to_api(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass(frozen=True)
class ModelConfig:
    model: str
    temperature: float = 0.2
    effort: str = "medium"
    max_tokens: int = 16000


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: LLMUsage
    model: str
    request_id: str | None = None
    notes: list[str] = field(default_factory=list)
    refusal_category: str | None = None

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def thinking_summary(self) -> str:
        return "\n".join(b.get("thinking", "") for b in self.content if b.get("type") == "thinking").strip()


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        config: ModelConfig,
        output_schema: dict[str, Any] | None = None,
    ) -> LLMResponse: ...
