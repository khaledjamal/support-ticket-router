"""The contenders: Jev through the official SDK, and LLMs through OpenRouter's chat API.

Every model gets the same message and the same allowed options. LLMs answer with a label and a stated
confidence from 0 to 100, under a JSON schema that only allows the listed options.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any, Protocol

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Score,
    ScoreAnswer,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
)

from triage.bench.data import Item, Question, Task

OPENROUTER_URL = "https://openrouter.ai/api/v1"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Answer:
    label: str | None
    confidence: float | None
    """0 to 1."""
    probability: float | None = None
    """Jev only: the probability the model gave its chosen option, alongside its separate `confidence`."""


@dataclass(frozen=True)
class Call:
    item_id: str
    answers: dict[str, Answer] = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    raw: str | None = None
    """LLMs only: the reply text, truncated, for inspecting failures."""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> "Call":
        data = json.loads(line)
        data["answers"] = {name: Answer(**answer) for name, answer in data["answers"].items()}
        return cls(**data)


class Model(Protocol):
    name: str

    async def ask(self, task: Task, item: Item) -> Call: ...

    async def aclose(self) -> None: ...


class JevModel:
    def __init__(
        self, client: AsyncTypeSafeClient, *, name: str, price_per_input_token: float | None
    ) -> None:
        self._client = client
        self.name = name
        self._price = price_per_input_token

    @staticmethod
    def questions(task: Task) -> dict[str, Choice | Score]:
        result: dict[str, Choice | Score] = {}
        for q in task.questions:
            descriptions = q.descriptions or (None,) * len(q.options)
            if q.kind == "choice":
                result[q.name] = Choice(
                    instructions=q.instructions, criteria=dict(zip(q.options, descriptions, strict=True))
                )
            else:
                result[q.name] = Score(
                    instructions=q.instructions, criteria=list(q.descriptions or q.options)
                )
        return result

    async def ask(self, task: Task, item: Item) -> Call:
        start = perf_counter()
        try:
            response = await self._client.system_one(state=item.state, questions=self.questions(task))
        except (TypeSafeAPIError, TypeSafeAPIConnectionError) as error:
            return Call(item.id, latency_ms=(perf_counter() - start) * 1000, error=str(error)[:300])
        latency_ms = (perf_counter() - start) * 1000

        answers = {}
        for q in task.questions:
            match response.answers.get(q.name):
                case ChoiceAnswer(choice=choice, confidence=confidence, probabilities=probabilities):
                    answers[q.name] = Answer(choice, confidence, probabilities.get(choice))
                case ScoreAnswer(score=score, confidence=confidence, probabilities=probabilities):
                    level = min(max(round(score), 0), len(q.options) - 1)
                    answers[q.name] = Answer(q.options[level], confidence, probabilities.get(level))
        tokens = response.usage.input_tokens
        cost = tokens * self._price if self._price is not None and tokens is not None else None
        return Call(item.id, answers, latency_ms, cost)

    async def aclose(self) -> None:
        await self._client.aclose()


def render_message(state: dict[str, str]) -> str:
    return "\n".join(f"{key.capitalize()}: {value}" for key, value in state.items())


def build_prompt(task: Task, item: Item) -> list[dict[str, str]]:
    sections = []
    for q in task.questions:
        order = " (ordered from lowest to highest)" if q.kind == "score" else ""
        lines = [f"### {q.name}", q.instructions, f"Options{order}:"]
        for option, description in zip(q.options, q.descriptions or (None,) * len(q.options), strict=True):
            useful = description and description.lower() != option.replace("_", " ").lower()
            lines.append(f"- {option}: {description}" if useful else f"- {option}")
        sections.append("\n".join(lines))
    reply = ", ".join(f'"{q.name}": {{"label": ..., "confidence": ...}}' for q in task.questions)
    user = (
        f"Message:\n<message>\n{render_message(item.state)}\n</message>\n\n"
        "Answer each question by choosing exactly one of its options, copied exactly. For each answer, give "
        "your confidence: the probability, in percent from 0 to 100, that your label is correct.\n\n"
        + "\n\n".join(sections)
        + f"\n\nReply with JSON only: {{{reply}}}"
    )
    return [
        {"role": "system", "content": "You classify customer-support messages for an automated system."},
        {"role": "user", "content": user},
    ]


def response_schema(task: Task) -> dict[str, Any]:
    answer = {
        q.name: {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": list(q.options)},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["label", "confidence"],
            "additionalProperties": False,
        }
        for q in task.questions
    }
    return {
        "type": "object",
        "properties": answer,
        "required": [q.name for q in task.questions],
        "additionalProperties": False,
    }


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def parse_reply(questions: tuple[Question, ...], text: str) -> dict[str, Answer]:
    """Read an LLM reply. A label outside the options is kept as given; scoring counts it as wrong."""
    try:
        data = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    answers = {}
    for q in questions:
        entry = data.get(q.name)
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        confidence = entry.get("confidence")
        confidence = (
            min(max(float(confidence), 0.0), 100.0) / 100
            if isinstance(confidence, int | float) and not isinstance(confidence, bool)
            else None
        )
        answers[q.name] = Answer(str(label) if label is not None else None, confidence)
    return answers


class OpenRouterModel:
    def __init__(self, model_id: str, *, api_key: str, http: httpx2.AsyncClient | None = None) -> None:
        self.name = model_id
        self._http = http or httpx2.AsyncClient(
            base_url=OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "support-ticket-router benchmark"},
            timeout=60,
        )

    def body(self, task: Task, item: Item) -> dict[str, Any]:
        return {
            "model": self.name,
            "messages": build_prompt(task, item),
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answers", "strict": True, "schema": response_schema(task)},
            },
            "reasoning": {"enabled": False},
            "usage": {"include": True},
        }

    async def ask(self, task: Task, item: Item) -> Call:
        start = perf_counter()
        error = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._http.post("/chat/completions", json=self.body(task, item))
            except httpx2.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    data = response.json()
                    if "choices" in data:
                        text = data["choices"][0]["message"].get("content") or ""
                        return Call(
                            item.id,
                            parse_reply(task.questions, text),
                            (perf_counter() - start) * 1000,
                            (data.get("usage") or {}).get("cost"),
                            raw=text[:500],
                        )
                    error = f"no choices in reply: {str(data)[:200]}"
                else:
                    error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if response.status_code not in RETRY_STATUSES:
                        break
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
        return Call(item.id, latency_ms=(perf_counter() - start) * 1000, error=error)

    async def aclose(self) -> None:
        await self._http.aclose()
