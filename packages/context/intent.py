"""Deterministic request classification + entity extraction.

Runs before any LLM call. It is cheap and predictable, and its output steers retrieval
(entity boosts) and is recorded in the trace, so "why did it fetch that?" has an
answer. The LLM still makes the actual decisions about which tools to call.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

IntentKind = Literal["action_request", "troubleshooting", "lookup", "question"]

_ID = re.compile(r"\b([A-Z]{1,4})-(\d{2,6})\b")
_ERROR_CODE = re.compile(r"\bE-[A-Z]{2,5}-\d{2,4}\b", re.IGNORECASE)
_CUSTOMER_PREFIXES = {"C", "ACC", "CUST"}
_JOB_PREFIXES = {"J", "JOB", "WO"}
_TICKET_PHRASES = (
    "create a ticket",
    "open a ticket",
    "file a ticket",
    "raise a ticket",
    "log a ticket",
    "create ticket",
    "open ticket",
    "escalate",
    "create a support ticket",
    "open a support ticket",
    "create a p1",
    "open a p1",
)
_TROUBLE_WORDS = (
    "fail", "error", "broken", "not working", "issue", "problem", "down", "outage", "can't", "cannot",
    "stuck", "offline", "won't", "doesn't", "crash", "alarm", "fault", "wrong", "incorrect", "not cooling",
    "too hot", "too cold", "leak", "noise", "late", "excursion",
)  # fmt: skip
_CRITICAL_WORDS = ("outage", "safety", "fire", "gas leak", "flood", "all sites", "p1", "critical")
_HIGH_WORDS = ("urgent", "asap", "immediately", "high priority", "escalate")


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    """Word-start prefix match: 'fail' matches 'failed'/'failure' but 'late' won't match 'related'."""
    return any(re.search(r"\b" + re.escape(w), text) for w in words)


@dataclass
class Intent:
    kind: IntentKind
    customer_ids: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    wants_ticket: bool = False
    urgency: Literal["critical", "high"] | None = None

    @property
    def entities(self) -> list[str]:
        return [*self.customer_ids, *self.job_ids, *self.error_codes]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "customer_ids": self.customer_ids,
            "job_ids": self.job_ids,
            "error_codes": self.error_codes,
            "wants_ticket": self.wants_ticket,
            "urgency": self.urgency,
        }


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def classify_intent(message: str) -> Intent:
    lowered = message.lower()
    error_codes = _unique(m.upper() for m in _ERROR_CODE.findall(message))
    customer_ids, job_ids = [], []
    for prefix, number in _ID.findall(message.upper()):
        ident = f"{prefix}-{number}"
        if prefix in _CUSTOMER_PREFIXES:
            customer_ids.append(ident)
        elif prefix in _JOB_PREFIXES:
            job_ids.append(ident)
    wants_ticket = any(p in lowered for p in _TICKET_PHRASES)
    if wants_ticket:
        kind: IntentKind = "action_request"
    elif _mentions(lowered, _TROUBLE_WORDS):
        kind = "troubleshooting"
    elif customer_ids or job_ids:
        kind = "lookup"
    else:
        kind = "question"
    urgency = "critical" if _mentions(lowered, _CRITICAL_WORDS) else "high" if _mentions(lowered, _HIGH_WORDS) else None
    return Intent(
        kind=kind,
        customer_ids=_unique(customer_ids),
        job_ids=_unique(job_ids),
        error_codes=error_codes,
        wants_ticket=wants_ticket,
        urgency=urgency,
    )
