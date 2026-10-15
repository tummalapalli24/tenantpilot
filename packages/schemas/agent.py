"""Structured objects passed between agent steps, validated with Pydantic.

The model's final answer is constrained to FINAL_ANSWER_JSON_SCHEMA (Claude structured
outputs, where the model supports it) and then validated again here. Every finding
must cite source ids ("K1" = knowledge chunk, "T1" = tool result) that actually exist in
the run; citations to unknown ids are flagged as unsupported.
"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


class Finding(BaseModel):
    statement: str
    source_ids: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    needs_human_followup: bool = False


# Hand-written (not model_json_schema()) so it stays inside the JSON Schema subset that
# structured outputs accept: every object closed, every property required.
FINAL_ANSWER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "source_ids"],
                "additionalProperties": False,
            },
        },
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "needs_human_followup": {"type": "boolean"},
    },
    "required": ["summary", "findings", "recommended_actions", "confidence", "needs_human_followup"],
    "additionalProperties": False,
}


class AgentAction(BaseModel):
    """One decision of the agent loop, as recorded in the trace.

    ``{"action": "tool_call", "tool": "get_customer", "arguments": {"customer_id": "C-104"}}``
    """

    action: Literal["tool_call", "final_answer", "request_approval"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None


class AnswerValidation(BaseModel):
    parsed: bool
    schema_valid: bool
    unsupported_source_ids: list[str] = Field(default_factory=list)
    error: str | None = None


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_final_answer(text: str, known_source_ids: set[str]) -> tuple[FinalAnswer, AnswerValidation]:
    """Parse + validate the model's final text. Never raises: a bad answer degrades to a
    low-confidence summary so the operator still sees something, and the trace records why."""
    raw = text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(raw)  # models without structured outputs may wrap JSON in prose
        try:
            data = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        return (
            FinalAnswer(summary=raw or "(empty response)", confidence="low", needs_human_followup=True),
            AnswerValidation(parsed=False, schema_valid=False, error="response was not JSON"),
        )
    try:
        answer = FinalAnswer.model_validate(data)
    except ValidationError as exc:
        return (
            FinalAnswer(summary=str(data.get("summary") or raw)[:4000], confidence="low", needs_human_followup=True),
            AnswerValidation(parsed=True, schema_valid=False, error=str(exc)[:500]),
        )
    cited = {sid for f in answer.findings for sid in f.source_ids}
    unsupported = sorted(sid for sid in cited if sid not in known_source_ids)
    return answer, AnswerValidation(parsed=True, schema_valid=True, unsupported_source_ids=unsupported)
