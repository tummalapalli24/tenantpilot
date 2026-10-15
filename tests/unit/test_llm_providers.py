"""LLM layer: model-aware request shaping (no network) and the offline planner."""

import json

import pytest
from anthropic.types import Message

from packages.schemas.agent import FINAL_ANSWER_JSON_SCHEMA, FinalAnswer
from services.agent.llm.anthropic_provider import AnthropicProvider, sanitize_for_echo
from services.agent.llm.base import LLMError, ModelConfig, ToolSpec
from services.agent.llm.models import get_provider
from services.agent.llm.offline_provider import OfflineHeuristicProvider

TOOLS = [ToolSpec("get_customer", "Get a customer", {"type": "object", "properties": {}}, True)]


def shape(model: str):
    provider = AnthropicProvider(api_key="test-key")
    return provider.build_request(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=TOOLS,
        config=ModelConfig(model=model, temperature=0.3, effort="low"),
        output_schema=FINAL_ANSWER_JSON_SCHEMA,
    )


def test_opus5_request_drops_temperature_and_uses_effort_format_thinking_fallbacks():
    kwargs, notes, fallbacks = shape("claude-opus-5")
    assert "temperature" not in kwargs and "extra_body" not in kwargs
    assert any("temperature" in n for n in notes)
    assert kwargs["output_config"]["effort"] == "low"
    assert kwargs["output_config"]["format"]["schema"] == FINAL_ANSWER_JSON_SCHEMA
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["cache_control"] == {"type": "ephemeral"}
    assert fallbacks is True


def test_haiku_request_sends_temperature_via_extra_body_without_effort():
    kwargs, notes, fallbacks = shape("claude-haiku-4-5")
    assert kwargs["extra_body"] == {"temperature": 0.3}
    assert "effort" not in kwargs.get("output_config", {})
    assert "thinking" not in kwargs and fallbacks is False


def test_unknown_claude_model_gets_the_conservative_shape():
    kwargs, notes, _ = shape("claude-future-9")
    assert "output_config" not in kwargs and "extra_body" not in kwargs
    assert any("structured outputs unsupported" in n for n in notes)


async def test_complete_converts_sdk_message_into_neutral_blocks():
    provider = AnthropicProvider(api_key="test-key")
    message = Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "toolu_1", "name": "get_customer", "input": {"customer_id": "C-1"}},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 100},
        }
    )

    async def fake_create(**kwargs):
        assert kwargs["model"] == "claude-haiku-4-5"
        return message

    provider.client.messages.create = fake_create
    response = await provider.complete(
        system="s",
        messages=[{"role": "user", "content": "x"}],
        tools=TOOLS,
        config=ModelConfig(model="claude-haiku-4-5"),
    )
    assert response.stop_reason == "tool_use"
    assert response.tool_uses == [
        {"type": "tool_use", "id": "toolu_1", "name": "get_customer", "input": {"customer_id": "C-1"}}
    ]
    assert response.usage.cache_read_tokens == 100 and response.text == "Checking."
    json.dumps(response.content)  # must be JSON-serializable: it is persisted for resume


def test_sanitize_for_echo_drops_pre_fallback_blocks():
    content = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": "partial"},
        {"type": "fallback"},
        {"type": "text", "text": "final"},
    ]
    assert sanitize_for_echo(content) == [{"type": "text", "text": "partial"}, {"type": "text", "text": "final"}]


def test_claude_models_require_an_api_key():
    with pytest.raises(LLMError) as err:
        get_provider("claude-opus-5")
    assert err.value.kind == "not_configured"
    assert isinstance(get_provider("offline-heuristic"), OfflineHeuristicProvider)


async def test_offline_planner_investigates_then_answers():
    provider = OfflineHeuristicProvider()
    tools = [
        ToolSpec(n, "", {}, n != "create_ticket")
        for n in ("get_customer", "list_open_jobs", "search_knowledge", "create_ticket")
    ]
    cfg = ModelConfig(model="offline-heuristic")
    messages = [
        {"role": "user", "content": "<request>\nCustomer C-104 install failed\n</request>\n\n<knowledge></knowledge>"}
    ]

    first = await provider.complete(system="", messages=messages, tools=tools, config=cfg)
    assert first.tool_uses[0]["name"] == "get_customer"

    def add_result(response, payload):
        messages.append({"role": "assistant", "content": response.content})
        tool = response.tool_uses[0]
        messages.append(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool["id"], "content": json.dumps(payload)}],
            }
        )

    add_result(
        first, {"source_id": "T1", "ok": True, "result": {"customer_id": "C-104", "name": "Harbor", "status": "ACTIVE"}}
    )
    second = await provider.complete(system="", messages=messages, tools=tools, config=cfg)
    assert second.tool_uses[0]["name"] == "list_open_jobs"
    add_result(
        second,
        {
            "source_id": "T2",
            "ok": True,
            "result": {
                "jobs": [
                    {"job_id": "J-774", "status": "FAILED", "error_code": "E-ACT-401", "job_type": "Installation"}
                ],
                "total": 1,
            },
        },
    )
    third = await provider.complete(system="", messages=messages, tools=tools, config=cfg)
    assert third.tool_uses[0]["name"] == "search_knowledge"
    assert "E-ACT-401" in third.tool_uses[0]["input"]["query"]
    add_result(
        third,
        {
            "source_id": "T3",
            "ok": True,
            "result": {
                "hits": [
                    {"content": "[Guide > E-ACT-401: Missing activation token]\nToken missing.\n1. Request a token."}
                ]
            },
        },
    )
    final = await provider.complete(system="", messages=messages, tools=tools, config=cfg)
    assert final.stop_reason == "end_turn" and not final.tool_uses
    answer = FinalAnswer.model_validate_json(final.text)
    assert "J-774" in answer.summary and answer.recommended_actions == ["Request a token."]
    assert answer.confidence == "high"
    cited = {s for f in answer.findings for s in f.source_ids}
    assert {"T1", "T2", "T3"} <= cited
