import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from triage.decisions import Route, Thresholds, certainty, route

THRESHOLDS = Thresholds(auto=0.85, review=0.60)


def choice(confidence: float) -> ChoiceAnswer:
    return ChoiceAnswer(
        choice="a", confidence=confidence, probabilities={"a": confidence, "b": 1 - confidence}
    )


def score(confidence: float) -> ScoreAnswer:
    return ScoreAnswer(
        score=1.0, confidence=confidence, legend={0: "x", 1: "y"}, probabilities={0: 0.0, 1: 1.0}
    )


@pytest.mark.parametrize(
    ("probability", "expected"), [(0.5, 0.0), (1.0, 1.0), (0.0, 1.0), (0.9, 0.8), (0.2, 0.6)]
)
def test_noul_certainty_is_distance_from_a_coin_flip(probability: float, expected: float) -> None:
    assert certainty(NoulAnswer(noul=probability)) == pytest.approx(expected)


def test_choice_and_score_certainty_is_their_confidence() -> None:
    assert certainty(choice(0.7)) == 0.7
    assert certainty(score(0.3)) == 0.3


@pytest.mark.parametrize(
    ("category", "urgency", "expected"),
    [
        (0.95, 0.90, Route.AUTO),
        (0.85, 0.85, Route.AUTO),  # thresholds are inclusive
        (0.95, 0.84, Route.REVIEW),
        (0.60, 0.99, Route.REVIEW),
        (0.59, 0.99, Route.HUMAN),
        (0.99, 0.10, Route.HUMAN),
    ],
)
def test_routes_on_the_least_certain_gated_answer(category: float, urgency: float, expected: Route) -> None:
    answers = {"category": choice(category), "urgency": score(urgency)}
    assert route(answers, THRESHOLDS, gated=["category", "urgency"]).route is expected


def test_reports_the_weakest_answer() -> None:
    decision = route(
        {"category": choice(0.95), "urgency": score(0.7)}, THRESHOLDS, gated=["category", "urgency"]
    )
    assert (decision.weakest, decision.certainty) == ("urgency", 0.7)
    assert "urgency" in decision.reason


def test_ungated_answers_do_not_block_automation() -> None:
    answers = {"category": choice(0.95), "risk": NoulAnswer(noul=0.5)}
    assert route(answers, THRESHOLDS, gated=["category"]).route is Route.AUTO


def test_missing_gated_answer_goes_to_a_human() -> None:
    decision = route({"category": choice(0.99)}, THRESHOLDS, gated=["category", "urgency"])
    assert decision.route is Route.HUMAN
    assert "urgency" in decision.reason


def test_requires_a_gated_question() -> None:
    with pytest.raises(ValueError, match="gated"):
        route({"category": choice(0.99)}, THRESHOLDS, gated=[])


@pytest.mark.parametrize(("auto", "review"), [(0.5, 0.6), (1.1, 0.5), (0.9, -0.1)])
def test_rejects_inconsistent_thresholds(auto: float, review: float) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        Thresholds(auto=auto, review=review)
