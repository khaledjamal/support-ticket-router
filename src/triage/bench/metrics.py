"""Accuracy and calibration: is the model right, and does its confidence tell you when it is?"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean, quantiles

from triage.bench.data import Task
from triage.bench.models import Call

CONFIDENT = 0.9
"""Answers at or above this confidence are the ones an automated system would act on without review."""
BINS = 10


@dataclass(frozen=True)
class Scored:
    correct: bool
    confidence: float | None
    """None when the model gave no usable confidence; such answers count for accuracy but not calibration."""
    valid: bool
    """The label was one of the allowed options."""


@dataclass(frozen=True)
class Bin:
    low: float
    high: float
    count: int
    mean_confidence: float
    accuracy: float


@dataclass(frozen=True)
class Metrics:
    answered: int
    errors: int
    """Calls that failed outright, after retries. Not counted in any other metric."""
    accuracy: float
    mean_confidence: float | None
    overconfidence: float | None
    """Mean confidence minus accuracy, over answers with a confidence. Positive: it claims more than it delivers."""
    ece: float | None
    """Expected calibration error: average gap between confidence and accuracy, weighted by bin size."""
    brier: float | None
    """Mean squared gap between confidence and being right (0 or 1). Lower is better."""
    confident_share: float | None
    """Share of answers at or above CONFIDENT: what automation would act on."""
    confident_accuracy: float | None
    """Accuracy of those answers: how often automation would be right."""
    invalid: int


def score(task: Task, calls: Iterable[Call], question: str) -> tuple[list[Scored], int]:
    """Score one question's answers against the labels. Returns the scored answers and the failed-call count."""
    labels = {item.id: item.labels[question] for item in task.items}
    options = set(next(q for q in task.questions if q.name == question).options)
    scored, errors = [], 0
    for call in calls:
        if call.error is not None:
            errors += 1
            continue
        answer = call.answers.get(question)
        if answer is None:
            scored.append(Scored(correct=False, confidence=None, valid=False))
        else:
            scored.append(
                Scored(answer.label == labels[call.item_id], answer.confidence, answer.label in options)
            )
    return scored, errors


def _rated(scored: Iterable[Scored]) -> list[tuple[float, bool]]:
    return [(s.confidence, s.correct) for s in scored if s.confidence is not None]


def reliability(scored: Sequence[Scored], bins: int = BINS) -> list[Bin]:
    """Group answers by stated confidence and compare each group's confidence with its accuracy."""
    groups: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for confidence, correct in _rated(scored):
        groups[min(int(confidence * bins), bins - 1)].append((confidence, correct))
    return [
        Bin(b / bins, (b + 1) / bins, len(group), mean(c for c, _ in group), mean(ok for _, ok in group))
        for b, group in enumerate(groups)
        if group
    ]


def expected_calibration_error(scored: Sequence[Scored], bins: int = BINS) -> float | None:
    groups = reliability(scored, bins)
    total = sum(group.count for group in groups)
    if not total:
        return None
    return sum(group.count * abs(group.mean_confidence - group.accuracy) for group in groups) / total


def compute(scored: Sequence[Scored], errors: int = 0) -> Metrics:
    rated = _rated(scored)
    confident = [ok for confidence, ok in rated if confidence >= CONFIDENT]
    mean_confidence = mean(c for c, _ in rated) if rated else None
    return Metrics(
        answered=len(scored),
        errors=errors,
        accuracy=mean(s.correct for s in scored) if scored else 0.0,
        mean_confidence=mean_confidence,
        overconfidence=(
            mean_confidence - mean(ok for _, ok in rated) if mean_confidence is not None else None
        ),
        ece=expected_calibration_error(scored),
        brier=mean((c - ok) ** 2 for c, ok in rated) if rated else None,
        confident_share=len(confident) / len(scored) if scored else None,
        confident_accuracy=mean(confident) if confident else None,
        invalid=sum(not s.valid for s in scored),
    )


def latency_percentiles(calls: Sequence[Call]) -> tuple[float | None, float | None]:
    latencies = [call.latency_ms for call in calls if call.error is None]
    if not latencies:
        return None, None
    if len(latencies) == 1:
        return latencies[0], latencies[0]
    cuts = quantiles(latencies, n=100, method="inclusive")
    return cuts[49], cuts[94]


def cost_per_thousand(calls: Sequence[Call]) -> float | None:
    costs = [call.cost_usd for call in calls if call.error is None]
    known = [cost for cost in costs if cost is not None]
    if not costs or len(known) != len(costs):
        return None
    return 1000 * mean(known)
