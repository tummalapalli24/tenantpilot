"""ContextBuilder: relevance floor, dedupe, hybrid ranking, token budget, rendering."""

from packages.context.builder import ContextBuilder
from packages.context.intent import classify_intent
from packages.knowledge.retriever import RetrievedChunk


def chunk(cid: str, content: str, score: float, tokens: int = 50, h: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id="d",
        document=f"{cid}.md",
        section="S",
        content=content,
        content_hash=h or cid,
        token_count=tokens,
        vector_score=score,
    )


def build(candidates, max_tokens=6000, request="Customer C-104 installation failed with E-ACT-401"):
    return ContextBuilder(max_context_tokens=max_tokens).build(
        request=request, tenant_name="Acme", tenant_instructions="Be precise.", candidates=candidates
    )


def test_drops_chunks_below_relevance_floor():
    bundle = build([chunk("a", "activation token missing E-ACT-401", 0.5), chunk("b", "unrelated", 0.001)])
    assert [e.chunk.chunk_id for e in bundle.selected] == ["a"]
    assert any("below relevance floor" in d["reason"] for d in bundle.dropped)


def test_removes_exact_and_near_duplicates_keeping_the_best_copy():
    text = "Request a new activation token through the provisioning queue and re-run commissioning"
    bundle = build(
        [
            chunk("best", text, 0.9, h="same"),
            chunk("exact", text, 0.8, h="same"),
            chunk("near", text + " today", 0.7, h="other"),
        ]
    )
    assert [e.chunk.chunk_id for e in bundle.selected] == ["best"]
    reasons = {d["chunk_id"]: d["reason"] for d in bundle.dropped}
    assert reasons["exact"].startswith("duplicate of best")
    assert reasons["near"].startswith("near-duplicate of best")


def test_entity_match_boosts_ranking():
    bundle = build(
        [
            chunk("generic", "Installation guide overview for controllers", 0.40),
            chunk("specific", "E-ACT-401: missing activation token on installation", 0.38),
        ]
    )
    assert bundle.selected[0].chunk.chunk_id == "specific"
    assert bundle.selected[0].entity_boost == 1.0
    assert bundle.selected[0].source_id == "K1"


def test_respects_token_budget_and_records_why():
    big = [chunk(f"c{i}", f"installation step {i} activation", 0.9 - i * 0.01, tokens=900) for i in range(6)]
    bundle = build(big, max_tokens=4000)
    assert bundle.usage["knowledge"] <= bundle.budget["knowledge"]
    assert len(bundle.selected) < 6
    assert any("over token budget" in d["reason"] for d in bundle.dropped)


def test_renders_sources_entities_and_tenant_instructions():
    bundle = build([chunk("a", "activation token E-ACT-401", 0.5)])
    assert '<source id="K1"' in bundle.user_message
    assert "customer_ids: C-104" in bundle.user_message
    assert "error_codes: E-ACT-401" in bundle.user_message
    assert "<tenant_instructions>\nBe precise." in bundle.system_prompt
    assert bundle.source_ids == {"K1"}
    attrs = bundle.trace_attributes()
    assert attrs["selected"][0]["reason"].startswith("rank 1")


def test_json_only_suffix_for_models_without_structured_outputs():
    builder = ContextBuilder(max_context_tokens=6000)
    assert "Respond with only that JSON object" in builder.system_prompt("Acme", "", json_only=True)
    assert "Respond with only that JSON object" not in builder.system_prompt("Acme", "", json_only=False)


def test_fit_tool_result_truncates_oversized_payloads():
    text, truncated, tokens = ContextBuilder.fit_tool_result("x" * 20_000, max_tokens=500)
    assert truncated and tokens <= 510 and "truncated" in text


def test_intent_classification_and_entities():
    intent = classify_intent("Please open a ticket for ACC-2041 and job J-774, error E-FW-118 - urgent!")
    assert intent.kind == "action_request" and intent.wants_ticket
    assert intent.customer_ids == ["ACC-2041"] and intent.job_ids == ["J-774"]
    assert intent.error_codes == ["E-FW-118"] and intent.urgency == "high"
    assert classify_intent("What is the SLA for Gold customers?").kind == "question"
    assert classify_intent("Customer C-101 unit is not working").kind == "troubleshooting"
    assert classify_intent("refrigerant outage at C-106").urgency == "critical"
