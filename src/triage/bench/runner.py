"""Run tasks against models with an on-disk cache, so a rerun never pays for an answer twice."""

import asyncio
import json
import re
import urllib.request
from pathlib import Path

from triage.bench.data import Task
from triage.bench.models import Call, Model, build_prompt

OUTPUT_TOKENS_PER_QUESTION = 30
"""Rough LLM reply size, for cost estimates. Jev's typed answers are free."""


def cache_path(cache_dir: Path, task: Task, model_name: str) -> Path:
    return cache_dir / task.name / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', model_name)}.jsonl"


def load(path: Path) -> dict[str, Call]:
    """Latest call per item. Failed calls are kept here but retried by `run`."""
    calls: dict[str, Call] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                call = Call.from_json(line)
                calls[call.item_id] = call
    return calls


class Pacer:
    """Spaces request starts at least 60/rpm seconds apart, for providers with per-minute limits."""

    def __init__(self, rpm: float | None) -> None:
        self._interval = 60 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = loop.time() + self._interval


async def run(
    task: Task,
    model: Model,
    path: Path,
    *,
    concurrency: int,
    limit: int | None = None,
    rpm: float | None = None,
) -> list[Call]:
    items = task.items[:limit] if limit else task.items
    calls = load(path)
    todo = [item for item in items if item.id not in calls or calls[item.id].error is not None]
    print(f"  {model.name}: {len(items) - len(todo)} cached, {len(todo)} to ask")
    path.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    pacer = Pacer(rpm)
    done = 0

    async def ask(item_index: int) -> None:
        nonlocal done
        async with semaphore:
            await pacer.wait()
            call = await model.ask(task, todo[item_index])
        calls[call.item_id] = call
        with path.open("a") as file:
            file.write(call.to_json() + "\n")
        done += 1
        if done % 50 == 0 or done == len(todo):
            print(f"    {done}/{len(todo)}")

    await asyncio.gather(*(ask(i) for i in range(len(todo))))
    return [calls[item.id] for item in items]


def openrouter_prices(model_ids: list[str]) -> dict[str, tuple[float, float]]:
    """Per-token (input, output) prices from OpenRouter's public catalogue. No key needed."""
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as response:
        catalogue = {m["id"]: m["pricing"] for m in json.load(response)["data"]}
    prices = {}
    for model_id in model_ids:
        pricing = catalogue.get(model_id)
        if pricing is None:  # Jev is not in the chat catalogue; its endpoint page has the price
            url = f"https://openrouter.ai/api/v1/models/{model_id}/endpoints"
            with urllib.request.urlopen(url, timeout=30) as response:
                endpoints = json.load(response)["data"]["endpoints"]
            pricing = endpoints[0]["pricing"] if endpoints else None
        if pricing is not None:
            prices[model_id] = (float(pricing["prompt"]), float(pricing["completion"]))
    return prices


def estimate_cost(
    task: Task,
    model_id: str,
    prices: dict[str, tuple[float, float]],
    *,
    items: int,
    is_jev: bool,
    cached: list[Call] | None = None,
) -> float | None:
    """Cost of asking `items` more items.

    Uses the average real cost of cached calls when there are any. Otherwise a rough guess from prompt
    characters / 4, which undercounts for some tokenizers, so probe a few items first.
    """
    known = [call.cost_usd for call in cached or () if call.error is None and call.cost_usd is not None]
    if known:
        return items * sum(known) / len(known)
    if model_id not in prices or not task.items:
        return None
    input_price, output_price = prices[model_id]
    sample = task.items[: min(20, len(task.items))]
    tokens = sum(len(m["content"]) for item in sample for m in build_prompt(task, item)) / 4 / len(sample)
    output = 0 if is_jev else OUTPUT_TOKENS_PER_QUESTION * len(task.questions)
    return items * (tokens * input_price + output * output_price)
