"""ContextBuilder: treats the prompt as a budgeted pipeline, not a dump.

    request -> intent/entities -> tenant config -> retrieve candidates -> dedupe
            -> rank (vector + lexical + entity boost) -> fit to token budget
            -> render structured prompt

Every decision is recorded: which chunks were selected and *why* (scores, rank),
which were dropped and why (duplicate / irrelevant / over budget), and how many tokens
each section used. That record is stored as a run step, so the trace screen can answer
"what exactly did the model see?".
"""

from dataclasses import dataclass, field
from typing import Any

from packages.context.intent import Intent, classify_intent
from packages.core.tokens import estimate_tokens, truncate_to_tokens
from packages.knowledge.embeddings import terms
from packages.knowledge.retriever import RetrievedChunk

PLATFORM_INSTRUCTIONS = """\
You are an operations assistant that TenantPilot deploys for {tenant_name}. You help \
field-operations engineers investigate customer issues using the tenant's knowledge \
base and tools.

How to work:
- Use the available tools to fetch customer and job data rather than guessing. Tool \
results are labeled T1, T2, ... and knowledge excerpts K1, K2, ...
- Ground each finding in those sources and cite their ids. If the sources do not \
support a conclusion, say so plainly and set needs_human_followup to true.
- Never invent customer, job, ticket, or error-code identifiers.
- Actions that change the customer's systems (such as creating a ticket) are reviewed \
by a human operator before they run. Request them through the tool when they are \
warranted; if one is rejected or fails, tell the operator what to do next.
- Keep the summary to 2-4 sentences and make recommended actions concrete.

Your final reply is a JSON object with: summary, findings (each with statement and \
source_ids), recommended_actions, confidence (low|medium|high), needs_human_followup."""

JSON_ONLY_SUFFIX = "\n\nRespond with only that JSON object - no prose before or after it."

# The floor is embedder-specific: the lexical hashing embedder produces low absolute
# cosine scores, so its floor is low. Retune it whenever the embedding model changes.
MIN_VECTOR_SCORE = 0.02
NEAR_DUPLICATE_JACCARD = 0.85


@dataclass
class Evidence:
    source_id: str
    chunk: RetrievedChunk
    lexical_score: float
    entity_boost: float
    final_score: float
    tokens: int
    reason: str


@dataclass
class ContextBundle:
    intent: Intent
    system_prompt: str
    user_message: str
    selected: list[Evidence]
    dropped: list[dict[str, Any]]
    budget: dict[str, int]
    usage: dict[str, int]
    tool_result_max_tokens: int
    source_ids: set[str] = field(default_factory=set)

    def trace_attributes(self) -> dict[str, Any]:
        return {
            "intent": self.intent.as_dict(),
            "budget": self.budget,
            "tokens": self.usage,
            "selected": [
                {
                    "source_id": e.source_id,
                    "chunk_id": e.chunk.chunk_id,
                    "document": e.chunk.document,
                    "section": e.chunk.section,
                    "vector_score": e.chunk.vector_score,
                    "lexical_score": e.lexical_score,
                    "entity_boost": e.entity_boost,
                    "final_score": e.final_score,
                    "tokens": e.tokens,
                    "reason": e.reason,
                    "preview": e.chunk.content[:280],
                }
                for e in self.selected
            ],
            "dropped": self.dropped,
            "tool_result_max_tokens": self.tool_result_max_tokens,
        }


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class ContextBuilder:
    def __init__(
        self, max_context_tokens: int, tool_reserve_ratio: float = 0.35, min_vector_score: float = MIN_VECTOR_SCORE
    ):
        self.max_context_tokens = max_context_tokens
        self.tool_reserve_ratio = tool_reserve_ratio
        self.min_vector_score = min_vector_score

    def system_prompt(self, tenant_name: str, tenant_instructions: str, json_only: bool) -> str:
        prompt = PLATFORM_INSTRUCTIONS.format(tenant_name=tenant_name)
        if tenant_instructions.strip():
            prompt += f"\n\n<tenant_instructions>\n{tenant_instructions.strip()}\n</tenant_instructions>"
        return prompt + (JSON_ONLY_SUFFIX if json_only else "")

    def build(
        self,
        *,
        request: str,
        tenant_name: str,
        tenant_instructions: str,
        candidates: list[RetrievedChunk],
        json_only: bool = False,
    ) -> ContextBundle:
        intent = classify_intent(request)
        system = self.system_prompt(tenant_name, tenant_instructions, json_only)
        system_tokens, request_tokens = estimate_tokens(system), estimate_tokens(request)
        tool_reserve = int(self.max_context_tokens * self.tool_reserve_ratio)
        knowledge_budget = max(0, self.max_context_tokens - system_tokens - request_tokens - tool_reserve - 150)

        query_terms = set(terms(request))
        entities = {e.lower() for e in intent.entities}
        dropped: list[dict[str, Any]] = []

        # 1) relevance floor + exact/near-duplicate removal (keep the higher-scoring copy)
        kept: list[tuple[RetrievedChunk, set[str]]] = []
        seen_hashes: dict[str, str] = {}
        for chunk in sorted(candidates, key=lambda c: c.vector_score, reverse=True):
            if chunk.vector_score < self.min_vector_score:
                dropped.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "document": chunk.document,
                        "reason": f"below relevance floor ({chunk.vector_score:.3f})",
                    }
                )
                continue
            if chunk.content_hash in seen_hashes:
                dropped.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "document": chunk.document,
                        "reason": f"duplicate of {seen_hashes[chunk.content_hash]}",
                    }
                )
                continue
            chunk_terms = set(terms(chunk.content))
            near = next((c for c, t in kept if _jaccard(t, chunk_terms) >= NEAR_DUPLICATE_JACCARD), None)
            if near is not None:
                dropped.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "document": chunk.document,
                        "reason": f"near-duplicate of {near.chunk_id}",
                    }
                )
                continue
            seen_hashes[chunk.content_hash] = chunk.chunk_id
            kept.append((chunk, chunk_terms))

        # 2) rank: hybrid of semantic-ish vector score, lexical overlap, and entity matches
        ranked: list[tuple[float, float, float, RetrievedChunk]] = []
        for chunk, chunk_terms in kept:
            lexical = round(len(query_terms & chunk_terms) / len(query_terms), 4) if query_terms else 0.0
            content_lower = chunk.content.lower()
            boost = 1.0 if entities and any(e in content_lower for e in entities) else 0.0
            final = round(0.6 * chunk.vector_score + 0.3 * lexical + 0.1 * boost, 4)
            ranked.append((final, lexical, boost, chunk))
        ranked.sort(key=lambda r: r[0], reverse=True)

        # 3) fit to the knowledge budget, highest value first
        selected: list[Evidence] = []
        used = 0
        for rank, (final, lexical, boost, chunk) in enumerate(ranked, start=1):
            cost = chunk.token_count + 20  # + tag overhead
            if used + cost > knowledge_budget:
                dropped.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "document": chunk.document,
                        "reason": f"over token budget (needs {cost}, {knowledge_budget - used} left)",
                    }
                )
                continue
            used += cost
            why = f"rank {rank}: vector={chunk.vector_score:.2f} lexical={lexical:.2f}"
            if boost:
                why += " +entity match"
            selected.append(Evidence(f"K{len(selected) + 1}", chunk, lexical, boost, final, cost, why))

        user_message = self._render(request, intent, selected)
        return ContextBundle(
            intent=intent,
            system_prompt=system,
            user_message=user_message,
            selected=selected,
            dropped=dropped,
            budget={
                "max_context_tokens": self.max_context_tokens,
                "knowledge": knowledge_budget,
                "tool_results_reserve": tool_reserve,
            },
            usage={
                "system": system_tokens,
                "request": request_tokens,
                "knowledge": used,
                "total_initial": estimate_tokens(system) + estimate_tokens(user_message),
            },
            tool_result_max_tokens=max(300, tool_reserve // 3),
            source_ids={e.source_id for e in selected},
        )

    @staticmethod
    def _render(request: str, intent: Intent, selected: list[Evidence]) -> str:
        parts = [f"<request>\n{request.strip()}\n</request>"]
        entity_lines = [
            f"customer_ids: {', '.join(intent.customer_ids) or 'none'}",
            f"job_ids: {', '.join(intent.job_ids) or 'none'}",
            f"error_codes: {', '.join(intent.error_codes) or 'none'}",
        ]
        parts.append("<detected_entities>\n" + "\n".join(entity_lines) + "\n</detected_entities>")
        if selected:
            docs = "\n".join(
                f'<source id="{e.source_id}" document="{e.chunk.document}" section="{e.chunk.section or ""}">\n'
                f"{e.chunk.content}\n</source>"
                for e in selected
            )
            parts.append(f"<knowledge>\n{docs}\n</knowledge>")
        else:
            parts.append("<knowledge>No relevant knowledge-base excerpts were found for this request.</knowledge>")
        return "\n\n".join(parts)

    @staticmethod
    def fit_tool_result(text: str, max_tokens: int) -> tuple[str, bool, int]:
        fitted, truncated = truncate_to_tokens(text, max_tokens)
        return fitted, truncated, estimate_tokens(fitted)
