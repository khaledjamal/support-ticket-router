"""Ticket triage: ask Jev, store the ticket with its decision, take human reviews, and report on the results."""

import random
from datetime import UTC, datetime
from statistics import quantiles

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from triage.db import Decision, Ticket
from triage.decisions import DecisionEngine, Route
from triage.questions import AT_RISK_PROBABILITY, QUESTIONS, urgency_level
from triage.schemas import ReviewIn, RouteAccuracy, Stats, TicketIn

LATENCY_WINDOW = 1000
"""Latency percentiles cover this many most recent decisions."""

_NEEDS_REVIEW = (Ticket.reviewed_at.is_(None)) & or_(Ticket.route != Route.AUTO, Ticket.audit)


async def triage(
    session: AsyncSession,
    engine: DecisionEngine,
    ticket_in: TicketIn,
    *,
    audit_rate: float = 0.0,
    rng: random.Random | None = None,
) -> Ticket:
    """Ask Jev about a ticket and store the outcome.

    A random `audit_rate` share of AUTO tickets is also sent for review. Reviewing only the uncertain tickets
    would never reveal how accurate the automated ones are.
    """
    state = {"subject": ticket_in.subject, "body": ticket_in.body}
    result = await engine.decide(state, QUESTIONS)

    ticket = Ticket(
        subject=ticket_in.subject,
        body=ticket_in.body,
        customer=ticket_in.customer,
        route=result.routing.route,
        route_reason=result.routing.reason,
        expected_cost=result.routing.expected_cost,
        audit=result.routing.route is Route.AUTO and (rng or random).random() < audit_rate,
    )
    match result.answers.get("category"):
        case ChoiceAnswer(choice=choice, confidence=confidence):
            ticket.category, ticket.category_confidence = choice, confidence
    match result.answers.get("urgency"):
        case ScoreAnswer(score=score, confidence=confidence):
            ticket.urgency_score, ticket.urgency_confidence = score, confidence
            ticket.urgency_level = urgency_level(score)
    match result.answers.get("churn_risk"):
        case NoulAnswer(noul=probability):
            ticket.churn_probability = probability
            ticket.at_risk = probability >= AT_RISK_PROBABILITY

    ticket.decision = Decision(
        backend=result.backend,
        policy=result.policy,
        model=result.model,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        answers={name: answer.model_dump(mode="json") for name, answer in result.answers.items()},
        error=result.error,
    )
    session.add(ticket)
    await session.commit()
    return ticket


async def list_tickets(session: AsyncSession, *, route: Route | None = None, limit: int = 50) -> list[Ticket]:
    query = select(Ticket).order_by(Ticket.id.desc()).limit(limit)
    if route is not None:
        query = query.where(Ticket.route == route)
    return list((await session.scalars(query)).all())


async def review_queue(session: AsyncSession, *, limit: int = 50) -> list[Ticket]:
    """Tickets waiting for a person, oldest first."""
    query = select(Ticket).where(_NEEDS_REVIEW).order_by(Ticket.id).limit(limit)
    return list((await session.scalars(query)).all())


async def review(session: AsyncSession, ticket: Ticket, review_in: ReviewIn) -> Ticket:
    """Record the correct answers. Reviewing again overwrites an earlier review."""
    ticket.reviewed_category = review_in.category
    ticket.reviewed_urgency_level = review_in.urgency_level
    ticket.reviewed_at = datetime.now(UTC)
    await session.commit()
    return ticket


async def _accuracy(session: AsyncSession) -> dict[Route, RouteAccuracy]:
    reviewed = (
        await session.execute(
            select(
                Ticket.route,
                Ticket.category == Ticket.reviewed_category,
                Ticket.urgency_level == Ticket.reviewed_urgency_level,
            ).where(Ticket.reviewed_at.is_not(None))
        )
    ).all()
    accuracy = {}
    for route in Route:
        rows = [(bool(cat), bool(urg)) for r, cat, urg in reviewed if r == route]
        accuracy[route] = RouteAccuracy(
            reviewed=len(rows),
            category_correct=sum(cat for cat, _ in rows),
            urgency_correct=sum(urg for _, urg in rows),
            both_correct=sum(cat and urg for cat, urg in rows),
        )
    return accuracy


async def stats(session: AsyncSession) -> Stats:
    counts = dict((await session.execute(select(Ticket.route, func.count()).group_by(Ticket.route))).all())
    by_route = {route: counts.get(route, 0) for route in Route}
    total = sum(by_route.values())
    errors = await session.scalar(
        select(func.count()).select_from(Decision).where(Decision.error.is_not(None))
    )
    pending = await session.scalar(select(func.count()).select_from(Ticket).where(_NEEDS_REVIEW))
    latencies = list(
        (
            await session.scalars(
                select(Decision.latency_ms)
                .where(Decision.error.is_(None))
                .order_by(Decision.id.desc())
                .limit(LATENCY_WINDOW)
            )
        ).all()
    )
    p50 = p95 = None
    if len(latencies) == 1:
        p50 = p95 = latencies[0]
    elif latencies:
        cuts = quantiles(latencies, n=100, method="inclusive")
        p50, p95 = cuts[49], cuts[94]
    return Stats(
        total=total,
        by_route=by_route,
        automation_rate=by_route[Route.AUTO] / total if total else None,
        errors=errors or 0,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        pending_review=pending or 0,
        accuracy=await _accuracy(session),
    )
