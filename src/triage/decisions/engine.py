"""Ask Jev a set of questions and route on the answers, never failing the caller on an API outage."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter

from typesafe_sdk import (
    Answer,
    AsyncTypeSafeClient,
    JSONContent,
    Question,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
)

from triage.decisions.policy import Route, RoutingDecision, RoutingPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionResult:
    answers: dict[str, Answer]
    """Empty when the request failed."""
    routing: RoutingDecision
    backend: str
    policy: str
    model: str | None
    latency_ms: float
    input_tokens: int | None
    error: str | None


class DecisionEngine:
    def __init__(self, client: AsyncTypeSafeClient, policy: RoutingPolicy, *, backend: str) -> None:
        self._client = client
        self.policy = policy
        self.backend = backend

    async def decide(self, state: JSONContent, questions: Mapping[str, Question]) -> DecisionResult:
        """Answer every question in one round trip, then route with the policy.

        API and connection failures (after the SDK's own retries) route to a human instead of raising,
        so an outage degrades to manual triage rather than lost tickets.
        """
        start = perf_counter()
        try:
            response = await self._client.system_one(state=state, questions=questions)
        except (TypeSafeAPIError, TypeSafeAPIConnectionError) as error:
            latency_ms = (perf_counter() - start) * 1000
            logger.warning(
                "decision failed backend=%s latency_ms=%.1f error=%r", self.backend, latency_ms, error
            )
            reason = f"decision engine unavailable ({type(error).__name__})"
            return DecisionResult(
                answers={},
                routing=RoutingDecision(Route.HUMAN, reason),
                backend=self.backend,
                policy=self.policy.name,
                model=None,
                latency_ms=latency_ms,
                input_tokens=None,
                error=str(error),
            )

        latency_ms = (perf_counter() - start) * 1000
        routing = self.policy.route(response.answers)
        logger.info(
            "decision backend=%s model=%s policy=%s latency_ms=%.1f route=%s reason=%r",
            self.backend,
            response.model,
            self.policy.name,
            latency_ms,
            routing.route,
            routing.reason,
        )
        return DecisionResult(
            answers=dict(response.answers),
            routing=routing,
            backend=self.backend,
            policy=self.policy.name,
            model=response.model,
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens,
            error=None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
