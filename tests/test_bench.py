"""The benchmark, offline: datasets from small local files, models behind fake transports."""

import asyncio
import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from triage.bench import data
from triage.bench.chart import reliability_svg
from triage.bench.data import Item, Question, Task
from triage.bench.metrics import Scored, compute, expected_calibration_error, reliability, score
from triage.bench.models import Answer, Call, JevModel, OpenRouterModel, build_prompt, parse_reply
from triage.bench.report import summarize
from triage.bench.runner import Pacer, cache_path, estimate_cost, load, run

QUEUE = Question("queue", "choice", "Which queue?", ("Billing", "Tech"))
PRIORITY = Question(
    "priority", "score", "How urgent?", ("low", "medium", "high"), ("Low: x", "Medium: y", "High: z")
)
TASK = Task(
    name="mini",
    title="Mini",
    source="test",
    questions=(QUEUE, PRIORITY),
    items=(
        Item("a", {"subject": "Refund", "body": "Charged twice"}, {"queue": "Billing", "priority": "high"}),
        Item("b", {"subject": "Crash", "body": "App crashes"}, {"queue": "Tech", "priority": "medium"}),
    ),
)


# metrics


def test_perfectly_calibrated_answers_have_zero_ece() -> None:
    scored = [Scored(True, 0.8, True)] * 8 + [Scored(False, 0.8, True)] * 2
    assert expected_calibration_error(scored) == pytest.approx(0)
    assert compute(scored).overconfidence == pytest.approx(0)


def test_overconfidence_and_confident_slice() -> None:
    scored = [Scored(True, 0.95, True)] * 6 + [Scored(False, 0.95, True)] * 4
    metrics = compute(scored)
    assert metrics.accuracy == pytest.approx(0.6)
    assert metrics.overconfidence == pytest.approx(0.35)
    assert metrics.ece == pytest.approx(0.35)
    assert (metrics.confident_share, metrics.confident_accuracy) == (1.0, pytest.approx(0.6))
    assert metrics.brier == pytest.approx((6 * 0.05**2 + 4 * 0.95**2) / 10)


def test_answers_without_confidence_count_for_accuracy_only() -> None:
    metrics = compute([Scored(True, 1.0, True), Scored(False, None, False)])
    assert metrics.accuracy == 0.5
    assert metrics.mean_confidence == 1.0
    assert metrics.invalid == 1


def test_reliability_bins_include_full_confidence() -> None:
    bins = reliability([Scored(True, 1.0, True), Scored(False, 0.05, True)])
    assert [(b.low, b.count, b.accuracy) for b in bins] == [(0.0, 1, 0), (0.9, 1, 1)]


def test_score_counts_failures_separately_and_wrong_or_invalid_labels_as_wrong() -> None:
    calls = [
        Call("a", {"queue": Answer("Billing", 0.9)}),
        Call("b", {"queue": Answer("Sales", 0.7)}),  # not an allowed option
        Call("a", error="HTTP 500"),
    ]
    scored, errors = score(TASK, calls, "queue")
    assert errors == 1
    assert [(s.correct, s.valid) for s in scored] == [(True, True), (False, False)]


# LLM prompts and replies


def test_prompt_lists_every_option_and_the_message() -> None:
    text = build_prompt(TASK, TASK.items[0])[1]["content"]
    for snippet in (
        "Charged twice",
        "- Billing",
        "- Tech",
        "- high: High: z",
        "lowest to highest",
        '"queue"',
    ):
        assert snippet in text


def test_parse_reply_reads_labels_and_scales_confidence() -> None:
    reply = '```json\n{"queue": {"label": "Tech", "confidence": 85}, "priority": {"label": "low", "confidence": 140}}\n```'
    answers = parse_reply(TASK.questions, reply)
    assert answers == {"queue": Answer("Tech", 0.85), "priority": Answer("low", 1.0)}


@pytest.mark.parametrize(
    "reply", ["not json", "[1, 2]", '{"queue": "Tech"}', '{"queue": {"label": "Tech", "confidence": true}}']
)
def test_parse_reply_tolerates_malformed_output(reply: str) -> None:
    answers = parse_reply(TASK.questions, reply)
    assert answers.get("queue") in (None, Answer("Tech", None))


def openrouter_with(handler) -> OpenRouterModel:
    http = httpx2.AsyncClient(base_url="http://test.invalid/api/v1", transport=httpx2.MockTransport(handler))
    return OpenRouterModel("test/model", api_key="unused", http=http)


def test_openrouter_model_sends_a_strict_schema_and_reads_cost() -> None:
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        content = '{"queue": {"label": "Billing", "confidence": 90}, "priority": {"label": "high", "confidence": 70}}'
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": {"cost": 0.0004}}
        )

    call = asyncio.run(openrouter_with(handler).ask(TASK, TASK.items[0]))

    schema = seen["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["queue"]["properties"]["label"]["enum"] == ["Billing", "Tech"]
    assert seen["temperature"] == 0
    assert call.answers["queue"] == Answer("Billing", 0.9)
    assert call.cost_usd == 0.0004


def test_openrouter_model_reports_non_retryable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    call = asyncio.run(
        openrouter_with(lambda request: httpx2.Response(400, text="bad model")).ask(TASK, TASK.items[0])
    )
    assert call.error is not None and "HTTP 400" in call.error


# Jev


def test_jev_model_maps_choice_and_score_answers() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["questions"]["priority"]["criteria"] == ["Low: x", "Medium: y", "High: z"]
        return httpx2.Response(
            200,
            json={
                "model": "jev-test",
                "usage": {"input_tokens": 100, "output_tokens": 0},
                "answers": {
                    "queue": {
                        "type": "choice",
                        "choice": "Tech",
                        "confidence": 0.8,
                        "probabilities": {"Tech": 0.75, "Billing": 0.25},
                    },
                    "priority": {
                        "type": "score",
                        "score": 1.6,
                        "confidence": 0.6,
                        "legend": {"0": "Low: x", "1": "Medium: y", "2": "High: z"},
                        "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                    },
                },
            },
        )

    client = AsyncTypeSafeClient(
        api_key="test",
        base_url="http://test.invalid",
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_retries=0),
    )
    call = asyncio.run(JevModel(client, name="jev", price_per_input_token=1e-6).ask(TASK, TASK.items[1]))

    assert call.answers["queue"] == Answer("Tech", 0.8, 0.75)
    assert call.answers["priority"] == Answer("high", 0.6, 0.7)  # expected score 1.6 rounds to level 2
    assert call.cost_usd == pytest.approx(100e-6)


# runner


class CountingModel:
    name = "counting"

    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.asked: list[str] = []
        self.fail = fail

    async def ask(self, task: Task, item: Item) -> Call:
        self.asked.append(item.id)
        if item.id in self.fail:
            return Call(item.id, error="boom")
        return Call(item.id, {"queue": Answer(item.labels["queue"], 0.9)}, latency_ms=5, cost_usd=0.001)

    async def aclose(self) -> None:
        pass


def test_runner_caches_answers_and_retries_only_failures(tmp_path: Path) -> None:
    path = cache_path(tmp_path, TASK, "vendor/model:v1")
    assert path.name == "vendor_model_v1.jsonl"

    first = CountingModel(fail={"b"})
    asyncio.run(run(TASK, first, path, concurrency=2))
    second = CountingModel()
    calls = asyncio.run(run(TASK, second, path, concurrency=2))

    assert sorted(first.asked) == ["a", "b"]
    assert second.asked == ["b"]  # "a" was cached; only the failure is asked again
    assert [c.error for c in calls] == [None, None]
    assert set(load(path)) == {"a", "b"}


def test_cost_estimate_prefers_real_cached_costs() -> None:
    cached = [Call("a", cost_usd=0.002), Call("b", cost_usd=0.004), Call("c", error="x")]
    assert estimate_cost(TASK, "m", {}, items=10, is_jev=False, cached=cached) == pytest.approx(0.03)
    assert estimate_cost(TASK, "unpriced", {}, items=10, is_jev=False) is None


def test_pacer_spaces_out_requests() -> None:
    async def timed() -> float:
        pacer = Pacer(rpm=600)  # one request per 0.1 s
        loop = asyncio.get_running_loop()
        start = loop.time()
        for _ in range(3):
            await pacer.wait()
        return loop.time() - start

    assert asyncio.run(timed()) >= 0.19


# datasets, from small local files


def test_banking77_samples_evenly_per_intent(tmp_path: Path) -> None:
    rows = [("text", "category")] + [
        (f"q{i} {intent}", intent) for intent in ("card_lost", "top_up") for i in range(10)
    ]
    (tmp_path / "banking77-test.csv").write_text("\n".join(",".join(row) for row in rows))

    task = data.banking77(tmp_path, per_intent=3)

    assert len(task.items) == 6
    assert task.questions[0].options == ("card_lost", "top_up")
    assert task.questions[0].descriptions == ("card lost", "top up")
    assert all(item.state["message"].endswith(item.labels["intent"]) for item in task.items)
    assert task.items == data.banking77(tmp_path, per_intent=3).items  # same seed, same sample


def test_tickets_keep_english_rows_and_unescape_newlines(tmp_path: Path) -> None:
    header = "subject,body,queue,priority,language"
    rows = [
        f"S{i},Line one\\nline two,{'Billing' if i % 2 else 'Tech'},{data.PRIORITIES[i % 3]},en"
        for i in range(6)
    ]
    rows.append("Hallo,Text,Billing,high,de")
    (tmp_path / "tickets-multi-lang-5-2-50.csv").write_text("\n".join([header, *rows]))

    task = data.tickets(tmp_path, sample=4)

    assert len(task.items) == 4
    assert all(item.state["subject"].startswith("S") for item in task.items)
    assert task.items[0].state["body"] == "Line one\nline two"
    assert task.questions[0].options == ("Billing", "Tech")
    assert task.questions[1].kind == "score"


# report and chart


def test_summary_and_chart_are_well_formed() -> None:
    calls = [
        Call(
            "a",
            {"queue": Answer("Billing", 0.95), "priority": Answer("high", 0.6)},
            latency_ms=10,
            cost_usd=0.001,
        ),
        Call(
            "b",
            {"queue": Answer("Billing", 0.95), "priority": Answer("medium", 0.9)},
            latency_ms=20,
            cost_usd=0.001,
        ),
    ]
    summary = summarize([TASK], {"mini": {"vendor/model": calls}})
    queue = summary["mini"]["questions"]["queue"]["vendor/model"]
    assert queue["metrics"]["accuracy"] == 0.5
    assert queue["cost_per_1k_usd"] == pytest.approx(1.0)

    svg = reliability_svg("Calibration, mini", summary["mini"]["questions"]["queue"])
    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "vendor/model" in svg
