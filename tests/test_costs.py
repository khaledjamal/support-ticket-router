import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from triage.costs import (
    CHURN_STAKES,
    MISROUTE_COST,
    UNDER_PRIORITY_COST,
    expected_mistake_cost,
    urgency_mistake_cost,
)
from triage.decisions import CostPolicy, Route
from triage.questions import GATED

LEVELS = 5


def category(p_top: float) -> ChoiceAnswer:
    return ChoiceAnswer(
        choice="billing", confidence=p_top, probabilities={"billing": p_top, "technical": 1 - p_top}
    )


def urgency(probabilities: dict[int, float]) -> ScoreAnswer:
    full = {level: probabilities.get(level, 0.0) for level in range(LEVELS)}
    score = sum(level * p for level, p in full.items())
    return ScoreAnswer(
        score=score,
        confidence=max(full.values()),
        legend={level: str(level) for level in range(LEVELS)},
        probabilities=full,
    )


def answers(p_category: float, urgency_probabilities: dict[int, float], churn: float | None = None) -> dict:
    result = {"category": category(p_category), "urgency": urgency(urgency_probabilities)}
    if churn is not None:
        result["churn_risk"] = NoulAnswer(noul=churn)
    return result


def test_certain_answers_cost_nothing() -> None:
    assert expected_mistake_cost(answers(1.0, {2: 1.0})) == 0


def test_misroute_cost_scales_with_the_chance_of_the_wrong_team() -> None:
    assert expected_mistake_cost(answers(0.8, {2: 1.0})) == pytest.approx(0.2 * MISROUTE_COST)


def test_under_prioritising_costs_more_than_over_prioritising() -> None:
    assert urgency_mistake_cost(acted=1, actual=3) > urgency_mistake_cost(acted=3, actual=1)
    assert urgency_mistake_cost(acted=0, actual=4) == UNDER_PRIORITY_COST[4]
    assert urgency_mistake_cost(acted=2, actual=2) == 0


def test_a_small_chance_of_critical_is_expensive() -> None:
    # Acted on as "normal" (level 2), but with a 10% chance it is really critical (level 4).
    cost = expected_mistake_cost(answers(1.0, {2: 0.9, 4: 0.1}))
    assert cost == pytest.approx(0.1 * UNDER_PRIORITY_COST[2])


def test_churn_risk_multiplies_the_stakes() -> None:
    calm = expected_mistake_cost(answers(0.8, {2: 1.0}, churn=0.0))
    leaving = expected_mistake_cost(answers(0.8, {2: 1.0}, churn=1.0))
    assert leaving == pytest.approx(calm * (1 + CHURN_STAKES))


def policy() -> CostPolicy:
    return CostPolicy(expected_mistake_cost, required=GATED, review_cost=2.0, escalate_cost=10.0)


@pytest.mark.parametrize(
    ("p_category", "urgency_probabilities", "churn", "expected"),
    [
        (1.0, {3: 1.0}, 0.9, Route.AUTO),  # certain, even for a customer about to leave
        (0.7, {2: 1.0}, 0.0, Route.AUTO),  # 30% misroute risk is $1.50, cheaper than a $2 review
        (0.7, {2: 1.0}, 0.5, Route.REVIEW),  # the same risk for a customer who might leave is $3
        (1.0, {1: 0.6, 4: 0.4}, 0.0, Route.REVIEW),  # 40% chance an outage is acted on as "normal": $4.60
        (1.0, {1: 0.6, 4: 0.4}, 1.0, Route.HUMAN),  # ... for a customer about to leave: $13.80
    ],
)
def test_cost_policy_routes(
    p_category: float, urgency_probabilities: dict[int, float], churn: float, expected: Route
) -> None:
    decision = policy().route(answers(p_category, urgency_probabilities, churn))
    assert decision.route is expected
    assert decision.expected_cost is not None
    assert f"${decision.expected_cost:.2f}" in decision.reason


def test_cost_policy_sends_missing_answers_to_a_human() -> None:
    decision = policy().route({"category": category(1.0)})
    assert decision.route is Route.HUMAN
    assert "urgency" in decision.reason


def test_cost_policy_rejects_inconsistent_costs() -> None:
    with pytest.raises(ValueError, match="review_cost <= escalate_cost"):
        CostPolicy(expected_mistake_cost, required=GATED, review_cost=5.0, escalate_cost=1.0)
