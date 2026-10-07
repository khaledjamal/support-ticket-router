"""The engine against the real SDK client, with the network replaced by an in-memory transport."""

import asyncio
from collections.abc import Callable

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from triage.decisions import (
    Backend,
    DecisionEngine,
    DecisionResult,
    Route,
    ThresholdPolicy,
    Thresholds,
    build_client,
)
from triage.questions import GATED, QUESTIONS

CLEAR_TICKET = {
    "subject": "Export crashes",
    "body": "Every time I export a report the app crashes with an error. It blocks my work and I need it fixed today.",
}
VAGUE_TICKET = {"subject": "Question", "body": "Hi, quick question about something I noticed earlier."}


def decide(client: AsyncTypeSafeClient, state: dict[str, str]) -> DecisionResult:
    async def run() -> DecisionResult:
        engine = DecisionEngine(client, ThresholdPolicy(Thresholds(), required=GATED), backend="test")
        try:
            return await engine.decide(state, QUESTIONS)
        finally:
            await engine.aclose()

    return asyncio.run(run())


def client_for(handler: Callable[[httpx2.Request], httpx2.Response]) -> AsyncTypeSafeClient:
    return AsyncTypeSafeClient(
        api_key="test",
        base_url="http://test.invalid",
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_retries=0),
    )


def test_fake_backend_answers_every_question_with_valid_types() -> None:
    result = decide(build_client(Backend.FAKE), CLEAR_TICKET)

    assert result.error is None
    assert set(result.answers) == set(QUESTIONS)
    category = result.answers["category"]
    assert category.choice in QUESTIONS["category"].criteria
    assert sum(category.probabilities.values()) == pytest.approx(1, abs=1e-3)
    assert 0 <= result.answers["urgency"].score <= len(QUESTIONS["urgency"].criteria) - 1
    assert 0 <= result.answers["churn_risk"].noul <= 1
    assert result.model == "fake-jev"
    assert result.latency_ms >= 0


def test_fake_backend_is_confident_on_clear_tickets_and_unsure_on_vague_ones() -> None:
    clear = decide(build_client(Backend.FAKE), CLEAR_TICKET)
    vague = decide(build_client(Backend.FAKE), VAGUE_TICKET)

    assert clear.answers["category"].choice == "technical"
    assert clear.routing.route is Route.AUTO
    assert vague.routing.route is Route.HUMAN


def test_fake_backend_is_deterministic() -> None:
    first = decide(build_client(Backend.FAKE), CLEAR_TICKET)
    second = decide(build_client(Backend.FAKE), CLEAR_TICKET)
    assert first.answers == second.answers


def test_server_error_routes_to_a_human_instead_of_raising() -> None:
    result = decide(
        client_for(lambda request: httpx2.Response(503, json={"error": "overloaded"})), CLEAR_TICKET
    )

    assert result.routing.route is Route.HUMAN
    assert "unavailable" in result.routing.reason
    assert result.answers == {}
    assert result.error is not None and "overloaded" in result.error


def test_connection_error_routes_to_a_human_instead_of_raising() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    result = decide(client_for(refuse), CLEAR_TICKET)

    assert result.routing.route is Route.HUMAN
    assert "TypeSafeAPIConnectionError" in result.routing.reason


def test_laya_backend_requires_a_base_url() -> None:
    with pytest.raises(ValueError, match="base URL"):
        build_client(Backend.LAYA)
