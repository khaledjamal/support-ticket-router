"""Turn Jev answers into a routing decision: by certainty thresholds, or by the expected cost of a mistake."""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from typesafe_sdk import Answer, NoulAnswer


class Route(StrEnum):
    AUTO = "auto"
    """Confident enough to act on without a person looking at it."""
    REVIEW = "review"
    """Act on it, but queue it for a quick human check."""
    HUMAN = "human"
    """Too uncertain or too risky (or no answer at all); a person handles it from scratch."""


def certainty(answer: Answer) -> float:
    """How sure an answer is, from 0 to 1.

    Choice and score answers carry Jev's own `confidence`. A noul answer only carries the
    probability of "yes", so its certainty is the distance from a coin flip: 0.5 -> 0, 0 or 1 -> 1.
    """
    if isinstance(answer, NoulAnswer):
        return abs(2 * answer.noul - 1)
    return answer.confidence


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    reason: str
    weakest: str | None = None
    """Threshold routing: the least certain required answer."""
    certainty: float | None = None
    """Threshold routing: that answer's certainty; the decision is only as trustworthy as its weakest part."""
    expected_cost: float | None = None
    """Cost routing: the expected cost of acting on the answers automatically."""


class RoutingPolicy(Protocol):
    name: str
    required: Collection[str]
    """Questions that must be answered; a missing one routes to a human."""

    def route(self, answers: Mapping[str, Answer]) -> RoutingDecision: ...

    def describe(self) -> str:
        """One line for dashboards and logs."""
        ...


def _missing(answers: Mapping[str, Answer], required: Collection[str]) -> RoutingDecision | None:
    missing = [name for name in required if name not in answers]
    return RoutingDecision(Route.HUMAN, f"no answer for {', '.join(missing)}") if missing else None


@dataclass(frozen=True)
class Thresholds:
    auto: float = 0.85
    """Every gated answer at or above this certainty -> AUTO."""
    review: float = 0.60
    """Any gated answer below this certainty -> HUMAN. In between -> REVIEW."""

    def __post_init__(self) -> None:
        if not 0 <= self.review <= self.auto <= 1:
            raise ValueError(
                f"thresholds must satisfy 0 <= review <= auto <= 1, got review={self.review}, auto={self.auto}"
            )


def route(
    answers: Mapping[str, Answer], thresholds: Thresholds, *, gated: Collection[str]
) -> RoutingDecision:
    """Pick a route from the least certain of the `gated` answers.

    Answers not listed in `gated` are informational (for example a risk flag) and never block automation.
    """
    if not gated:
        raise ValueError("at least one gated question is required")
    if missing := _missing(answers, gated):
        return missing

    weakest = min(gated, key=lambda name: certainty(answers[name]))
    lowest = certainty(answers[weakest])
    if lowest >= thresholds.auto:
        decision = Route.AUTO, f"all answers >= {thresholds.auto:.2f} certain"
    elif lowest >= thresholds.review:
        decision = Route.REVIEW, f"{weakest} is {lowest:.2f} certain"
    else:
        decision = Route.HUMAN, f"{weakest} is only {lowest:.2f} certain"
    return RoutingDecision(*decision, weakest=weakest, certainty=lowest)


@dataclass(frozen=True)
class ThresholdPolicy:
    """Automate when every gated answer is certain enough. Simple, but blind to what a mistake costs."""

    thresholds: Thresholds
    required: Collection[str]
    name: str = "threshold"

    def route(self, answers: Mapping[str, Answer]) -> RoutingDecision:
        return route(answers, self.thresholds, gated=self.required)

    def describe(self) -> str:
        return f"threshold routing: auto ≥ {self.thresholds.auto:.2f}, human < {self.thresholds.review:.2f}"


@dataclass(frozen=True)
class CostPolicy:
    """Automate when the expected cost of a mistake is below the cost of having a person check.

    `expected_cost` turns the answers into the expected cost of acting on them unreviewed. This is only
    meaningful when the probabilities are calibrated: it multiplies the chance of being wrong by what being
    wrong costs, so stakes and uncertainty both count.
    """

    expected_cost: Callable[[Mapping[str, Answer]], float]
    required: Collection[str]
    review_cost: float
    """What a quick human check costs. Below this, automation is the cheaper bet -> AUTO."""
    escalate_cost: float
    """Above this expected cost, a quick check isn't enough -> HUMAN. In between -> REVIEW."""
    name: str = "cost"

    def __post_init__(self) -> None:
        if not 0 <= self.review_cost <= self.escalate_cost:
            raise ValueError(
                "costs must satisfy 0 <= review_cost <= escalate_cost, "
                f"got review_cost={self.review_cost}, escalate_cost={self.escalate_cost}"
            )

    def route(self, answers: Mapping[str, Answer]) -> RoutingDecision:
        if missing := _missing(answers, self.required):
            return missing
        cost = self.expected_cost(answers)
        if cost <= self.review_cost:
            decision = Route.AUTO, f"expected mistake cost ${cost:.2f} ≤ review ${self.review_cost:.2f}"
        elif cost <= self.escalate_cost:
            decision = Route.REVIEW, f"expected mistake cost ${cost:.2f} > review ${self.review_cost:.2f}"
        else:
            decision = Route.HUMAN, f"expected mistake cost ${cost:.2f} > ${self.escalate_cost:.2f}"
        return RoutingDecision(*decision, expected_cost=cost)

    def describe(self) -> str:
        return f"cost routing: review ${self.review_cost:.2f}, escalate ${self.escalate_cost:.2f}"
