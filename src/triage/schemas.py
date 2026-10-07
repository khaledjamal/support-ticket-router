from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from triage.decisions import Route

Category = Literal["billing", "technical", "account", "shipping", "feedback"]
UrgencyLevel = Literal["can wait", "low", "normal", "high", "critical"]


class TicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    customer: str | None = Field(None, max_length=200)


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    backend: str
    policy: str
    model: str | None
    latency_ms: float
    input_tokens: int | None
    answers: dict[str, Any]
    error: str | None


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    subject: str
    body: str
    customer: str | None
    category: str | None
    category_confidence: float | None
    urgency_score: float | None
    urgency_level: str | None
    urgency_confidence: float | None
    churn_probability: float | None
    at_risk: bool
    route: Route
    route_reason: str
    expected_cost: float | None
    audit: bool
    needs_review: bool
    reviewed_at: datetime | None
    reviewed_category: str | None
    reviewed_urgency_level: str | None
    decision: DecisionOut


class ReviewIn(BaseModel):
    """The correct answers for a ticket, as judged by a person."""

    category: Category
    urgency_level: UrgencyLevel


class RouteAccuracy(BaseModel):
    reviewed: int
    category_correct: int
    urgency_correct: int
    both_correct: int
    """Tickets where the automatic handling would have been right."""


class Stats(BaseModel):
    total: int
    by_route: dict[Route, int]
    automation_rate: float | None
    """Share of tickets routed AUTO; None before the first ticket."""
    errors: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    pending_review: int
    accuracy: dict[Route, RouteAccuracy]
    """Jev's answers checked against human reviews. For AUTO this comes from the audit sample only."""
