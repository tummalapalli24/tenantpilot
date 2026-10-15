"""Tool authorization + argument validation + final-answer validation."""

from types import SimpleNamespace

from packages.schemas.agent import FinalAnswer, parse_final_answer
from packages.schemas.api import CatalogTool
from services.agent.policy import authorize, resolve_policies, validate_arguments

TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_id": {"type": "string"},
        "summary": {"type": "string", "minLength": 10},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
    },
    "required": ["customer_id", "summary", "priority"],
}
CATALOG = [
    CatalogTool(name="get_customer", description="", input_schema={}, read_only=True, risk="read"),
    CatalogTool(name="create_ticket", description="", input_schema=TICKET_SCHEMA, read_only=False, risk="write"),
    CatalogTool(name="search_knowledge", description="", input_schema={}, read_only=True, risk="read"),
]


def perm(enabled: bool, requires_approval: bool = False):
    return SimpleNamespace(enabled=enabled, requires_approval=requires_approval)


def test_tools_without_a_permission_row_are_disabled_by_default():
    policies = resolve_policies(CATALOG, {}, approval_required=True)
    assert all(not p.enabled for p in policies.values())
    assert authorize(policies, "get_customer").outcome == "deny"


def test_write_tools_need_approval_reads_do_not():
    policies = resolve_policies(CATALOG, {"get_customer": perm(True), "create_ticket": perm(True)}, True)
    assert authorize(policies, "get_customer").outcome == "allow"
    decision = authorize(policies, "create_ticket")
    assert decision.outcome == "approval" and "requires human approval" in decision.reason


def test_admin_can_force_approval_on_a_read_tool():
    policies = resolve_policies(CATALOG, {"search_knowledge": perm(True, requires_approval=True)}, True)
    assert authorize(policies, "search_knowledge").outcome == "approval"


def test_unknown_tool_is_denied():
    policies = resolve_policies(CATALOG, {"get_customer": perm(True)}, True)
    assert authorize(policies, "delete_everything").outcome == "deny"


def test_argument_validation_rejects_bad_enum_and_missing_fields():
    ok = validate_arguments(TICKET_SCHEMA, {"customer_id": "C-1", "summary": "Controller offline", "priority": "high"})
    assert ok is None
    bad_enum = validate_arguments(
        TICKET_SCHEMA, {"customer_id": "C-1", "summary": "Controller offline", "priority": "URGENTTT"}
    )
    assert bad_enum and "priority" in bad_enum
    missing = validate_arguments(TICKET_SCHEMA, {"customer_id": "C-1"})
    assert missing and "required" in missing
    assert validate_arguments(TICKET_SCHEMA, ["not", "an", "object"]) == "arguments must be a JSON object"


def test_parse_final_answer_valid_and_citation_check():
    text = (
        '{"summary": "J-774 failed.", "findings": [{"statement": "x", "source_ids": ["T1", "K9"]}], '
        '"recommended_actions": ["a"], "confidence": "high", "needs_human_followup": false}'
    )
    answer, validation = parse_final_answer(text, known_source_ids={"T1", "K1"})
    assert isinstance(answer, FinalAnswer) and answer.confidence == "high"
    assert validation.schema_valid and validation.unsupported_source_ids == ["K9"]


def test_parse_final_answer_extracts_json_wrapped_in_prose():
    text = (
        'Here you go:\n{"summary": "ok", "findings": [], "recommended_actions": [], "confidence": "low", '
        '"needs_human_followup": true}\nThanks'
    )
    answer, validation = parse_final_answer(text, set())
    assert validation.parsed and answer.summary == "ok"


def test_parse_final_answer_degrades_gracefully_on_garbage():
    answer, validation = parse_final_answer("I think the unit is broken.", set())
    assert not validation.parsed and answer.summary == "I think the unit is broken."
    assert answer.needs_human_followup and answer.confidence == "low"
    _, schema_bad = parse_final_answer('{"summary": "x", "confidence": "certain"}', set())
    assert schema_bad.parsed and not schema_bad.schema_valid
