"""Canonical business objects returned by tools.

Every customer names things differently (Acme's REST API says ``customer_ref`` and
``full_name``; BetaCorp's database says ``account_ref`` and ``org_name``). Connectors map
each customer's shape into these models, so the agent, prompts and evals only ever see
one vocabulary. Adding a customer means writing a mapping, not changing the agent.
"""

from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["low", "medium", "high", "critical"]


class Customer(BaseModel):
    customer_id: str
    name: str
    status: str | None = None
    tier: str | None = None
    region: str | None = None
    primary_contact: str | None = None
    open_ticket_count: int | None = None
    notes: str | None = None
    source: str = Field(description="Connector that produced this record")


class Job(BaseModel):
    job_id: str
    customer_id: str
    job_type: str | None = None
    status: str
    scheduled_for: str | None = None
    technician: str | None = None
    error_code: str | None = None
    notes: str | None = None


class JobList(BaseModel):
    customer_id: str
    jobs: list[Job]
    total: int
    truncated: bool = False
    source: str


class Ticket(BaseModel):
    ticket_id: str
    customer_id: str
    summary: str
    priority: Priority
    status: str
    created_at: str | None = None
    source: str


class KnowledgeHit(BaseModel):
    chunk_id: str
    document: str
    section: str | None = None
    score: float
    content: str


class KnowledgeSearchResult(BaseModel):
    query: str
    hits: list[KnowledgeHit]
