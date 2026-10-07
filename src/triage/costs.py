"""What a triage mistake costs, for cost-based routing.

The dollar figures are illustrative assumptions, not measurements: replace them with your own support costs.
What matters is their shape. Under-prioritising a ticket is far worse than over-prioritising it, and every
mistake costs more with a customer who is about to leave.
"""

from collections.abc import Mapping

from typesafe_sdk import Answer, ChoiceAnswer, NoulAnswer, ScoreAnswer

from triage.questions import URGENCY_LEVELS, urgency_level

MISROUTE_COST = 5.0
"""Sending a ticket to the wrong team: it bounces between queues and the customer waits."""

UNDER_PRIORITY_COST = (0.0, 3.0, 10.0, 30.0, 60.0)
"""Cost of acting on an urgency that is 0, 1, 2, 3 or 4 levels too low; e.g. treating an outage as "can wait"."""

OVER_PRIORITY_COST_PER_LEVEL = 1.0
"""Each level too high wastes some attention that another ticket needed."""

CHURN_STAKES = 2.0
"""A customer certain to leave makes any mistake (1 + 2) = 3x as costly."""


def urgency_mistake_cost(acted: int, actual: int) -> float:
    if actual > acted:
        return UNDER_PRIORITY_COST[actual - acted]
    return OVER_PRIORITY_COST_PER_LEVEL * (acted - actual)


def expected_mistake_cost(answers: Mapping[str, Answer]) -> float:
    """Expected cost of acting on Jev's category and urgency answers without a person checking them.

    Uses each answer's full probability distribution, not just its top pick: a ticket whose urgency is
    probably "normal" but possibly "critical" carries the cost of that tail.
    """
    category, urgency, churn = answers["category"], answers["urgency"], answers.get("churn_risk")
    if not isinstance(category, ChoiceAnswer) or not isinstance(urgency, ScoreAnswer):
        raise TypeError("expected a choice answer for category and a score answer for urgency")

    misroute = (1 - category.probabilities.get(category.choice, 0.0)) * MISROUTE_COST
    acted = URGENCY_LEVELS.index(urgency_level(urgency.score))
    wrong_urgency = sum(
        probability * urgency_mistake_cost(acted, actual)
        for actual, probability in urgency.probabilities.items()
    )
    stakes = 1 + CHURN_STAKES * churn.noul if isinstance(churn, NoulAnswer) else 1.0
    return (misroute + wrong_urgency) * stakes
