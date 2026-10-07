"""Benchmark datasets: downloaded once into data/ (git-ignored) and sampled with a fixed seed.

- Banking77 (PolyAI, CC BY 4.0): 3,080 human-written banking support questions, 77 intents.
- Customer support tickets (Tobias Bueck, CC BY-NC 4.0): synthetic, AI-generated support emails with a queue
  and a priority. Closer to the app's own questions, but the labels were generated too, so weigh it lower.
"""

import csv
import random
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BANKING77_URL = (
    "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/test.csv"
)
TICKETS_URL = (
    "https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets/resolve/main/"
    "aa_dataset-tickets-multi-lang-5-2-50-version.csv"
)
SEED = 7


@dataclass(frozen=True)
class Question:
    name: str
    kind: Literal["choice", "score"]
    """A score question's options are ordered, lowest first."""
    instructions: str
    options: tuple[str, ...]
    descriptions: tuple[str | None, ...] | None = None


@dataclass(frozen=True)
class Item:
    id: str
    state: dict[str, str]
    """What the model sees."""
    labels: dict[str, str]
    """The correct option for each question."""


@dataclass(frozen=True)
class Task:
    name: str
    title: str
    source: str
    questions: tuple[Question, ...]
    items: tuple[Item, ...]


def _download(url: str, path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url}")
        partial = path.with_suffix(".partial")
        urllib.request.urlretrieve(url, partial)
        partial.rename(path)
    return path


def banking77(data_dir: Path, per_intent: int = 6) -> Task:
    path = _download(BANKING77_URL, data_dir / "banking77-test.csv")
    by_intent: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as file:
        for row_number, row in enumerate(csv.DictReader(file)):
            by_intent[row["category"]].append((row_number, row["text"]))
    rng = random.Random(SEED)
    intents = tuple(sorted(by_intent))
    items = []
    for intent in intents:
        for row_number, text in rng.sample(by_intent[intent], per_intent):
            items.append(Item(f"b77-{row_number}", {"message": text}, {"intent": intent}))
    question = Question(
        name="intent",
        kind="choice",
        instructions="What is this online-banking customer asking about?",
        options=intents,
        descriptions=tuple(intent.replace("_", " ") for intent in intents),
    )
    return Task(
        name="banking77",
        title=f"Banking77: {len(items)} questions, 77 intents ({per_intent} per intent)",
        source="PolyAI Banking77 test split, human-written, CC BY 4.0",
        questions=(question,),
        items=tuple(items),
    )


PRIORITIES = ("low", "medium", "high")
PRIORITY_RUBRIC = (
    "Low: a question, request or minor issue that can wait",
    "Medium: a real problem or request that should be handled in the normal course of work",
    "High: urgent; blocks work, affects many users, involves security or money, or needs attention today",
)


def tickets(data_dir: Path, sample: int = 300) -> Task:
    path = _download(TICKETS_URL, data_dir / "tickets-multi-lang-5-2-50.csv")
    with path.open(encoding="utf-8") as file:
        rows = [
            (row_number, row)
            for row_number, row in enumerate(csv.DictReader(file))
            if row["language"] == "en" and row["priority"] in PRIORITIES
        ]
    queues = tuple(sorted({row["queue"] for _, row in rows}))
    items = [
        Item(
            f"tkt-{row_number}",
            {"subject": row["subject"], "body": row["body"].replace("\\n", "\n")},
            {"queue": row["queue"], "priority": row["priority"]},
        )
        for row_number, row in random.Random(SEED).sample(rows, sample)
    ]
    return Task(
        name="tickets",
        title=f"Support tickets: {len(items)} English emails, {len(queues)} queues, 3 priorities",
        source="Tobi-Bueck/customer-support-tickets, synthetic (AI-generated), CC BY-NC 4.0",
        questions=(
            Question("queue", "choice", "Which support queue should handle this ticket?", queues, None),
            Question("priority", "score", "How urgent is this ticket?", PRIORITIES, PRIORITY_RUBRIC),
        ),
        items=tuple(items),
    )


TASKS = {"banking77": banking77, "tickets": tickets}
