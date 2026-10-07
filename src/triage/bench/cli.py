"""`uv run triage-bench run|report`: ask the models, then score them."""

import argparse
import asyncio
import sys
from pathlib import Path

from triage.bench import report as report_module
from triage.bench.data import TASKS, Task
from triage.bench.models import JevModel, Model, OpenRouterModel
from triage.bench.runner import cache_path, estimate_cost, load, openrouter_prices, run
from triage.config import Settings
from triage.decisions import Backend, build_client

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "bench-cache"
RESULTS_DIR = ROOT / "results" / "benchmark"
DEFAULT_MODELS = ("jev", "openai/gpt-4.1-mini", "anthropic/claude-haiku-4.5", "google/gemini-2.5-flash")
DEFAULT_JEV_MODEL = "typesafe/jev-1.13"


def _jev_id(settings: Settings) -> str:
    return settings.model or DEFAULT_JEV_MODEL


def _model_ids(names: list[str], settings: Settings) -> dict[str, str]:
    """Display name -> model id. "jev" stands for the configured Jev model."""
    return {(_jev_id(settings) if name == "jev" else name): name for name in names}


def _build(model_id: str, name: str, settings: Settings, prices: dict[str, tuple[float, float]]) -> Model:
    if name == "jev":
        key = settings.typesafe_api_key
        client = build_client(
            Backend.JEV,
            api_key=key.get_secret_value() if key else None,
            base_url=settings.jev_base_url,
            model=model_id,
            timeout=30,
        )
        price = (
            prices.get(model_id, (None, None))[0]
            if "openrouter.ai" in (settings.jev_base_url or "")
            else None
        )
        return JevModel(client, name=model_id, price_per_input_token=price)
    key = settings.openrouter_key()
    if key is None:
        sys.exit("no OpenRouter key: set OPENROUTER_API_KEY (or route Jev through OpenRouter)")
    return OpenRouterModel(model_id, api_key=key)


def _pending(task: Task, model_id: str, limit: int | None) -> int:
    calls = load(cache_path(CACHE_DIR, task, model_id))
    items = task.items[:limit] if limit else task.items
    return sum(1 for item in items if item.id not in calls or calls[item.id].error is not None)


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    tasks = [TASKS[name](DATA_DIR) for name in args.tasks]
    models = _model_ids(args.models, settings)
    prices = openrouter_prices(list(models))

    print(f"{'task':<10} {'model':<32} {'to ask':>6} {'est. cost':>10}")
    total = 0.0
    for task in tasks:
        for model_id, name in models.items():
            pending = _pending(task, model_id, args.limit)
            cached = list(load(cache_path(CACHE_DIR, task, model_id)).values())
            cost = estimate_cost(task, model_id, prices, items=pending, is_jev=name == "jev", cached=cached)
            total += cost or 0
            shown = f"${cost:.3f}" if cost is not None else "unknown"
            print(f"{task.name:<10} {model_id:<32} {pending:>6} {shown:>10}")
    print(f"estimated total: ${total:.2f} (budget ${args.budget:.2f})")
    if args.dry_run:
        return
    if total > args.budget:
        sys.exit("estimate exceeds the budget; raise --budget or narrow --tasks/--models/--limit")

    built = {model_id: _build(model_id, name, settings, prices) for model_id, name in models.items()}
    try:
        for task in tasks:
            print(f"\n{task.title}")
            await asyncio.gather(
                *(
                    run(
                        task,
                        model,
                        cache_path(CACHE_DIR, task, model_id),
                        concurrency=args.concurrency,
                        limit=args.limit,
                        rpm=args.rpm,
                    )
                    for model_id, model in built.items()
                )
            )
    finally:
        for model in built.values():
            await model.aclose()


def _report(args: argparse.Namespace) -> None:
    settings = Settings()
    tasks = [TASKS[name](DATA_DIR) for name in args.tasks]
    models = _model_ids(args.models, settings)
    results = {
        task.name: {
            model_id: list(load(cache_path(CACHE_DIR, task, model_id)).values()) for model_id in models
        }
        for task in tasks
    }
    path = report_module.write(tasks, results, RESULTS_DIR, jev_model=_jev_id(settings))
    print(path.read_text())
    print(f"written to {path.relative_to(ROOT)} and {RESULTS_DIR.relative_to(ROOT)}/")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="triage-bench", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("run", "report"):
        sub = commands.add_parser(action)
        sub.add_argument("--tasks", nargs="+", choices=list(TASKS), default=list(TASKS))
        sub.add_argument(
            "--models", nargs="+", default=list(DEFAULT_MODELS), help='"jev" or OpenRouter model ids'
        )
        if action == "run":
            sub.add_argument("--limit", type=int, help="only the first N items of each task (probes)")
            sub.add_argument("--concurrency", type=int, default=8)
            sub.add_argument(
                "--rpm", type=float, help="at most this many requests per minute per model (rate limits)"
            )
            sub.add_argument(
                "--budget", type=float, default=2.0, help="abort if the estimate exceeds this, in USD"
            )
            sub.add_argument("--dry-run", action="store_true", help="estimate the cost and stop")
    args = parser.parse_args(argv)
    if args.action == "run":
        asyncio.run(_run(args))
    else:
        _report(args)
