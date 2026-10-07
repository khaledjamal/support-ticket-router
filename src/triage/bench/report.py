"""Write the benchmark results: a Markdown report and a JSON summary under results/benchmark/."""

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from triage.bench.chart import reliability_svg
from triage.bench.data import Task
from triage.bench.metrics import (
    CONFIDENT,
    compute,
    cost_per_thousand,
    latency_percentiles,
    reliability,
    score,
)
from triage.bench.models import Call


def _pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "–"
    return f"{100 * value:+.1f}" if signed else f"{100 * value:.1f}%"


def _ms(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "–"


def summarize(tasks: list[Task], results: dict[str, dict[str, list[Call]]]) -> dict:
    summary: dict = {}
    for task in tasks:
        summary[task.name] = {"title": task.title, "source": task.source, "questions": {}}
        for question in task.questions:
            per_model = {}
            for model_id, calls in results[task.name].items():
                if not calls:
                    continue
                scored, errors = score(task, calls, question.name)
                p50, p95 = latency_percentiles(calls)
                per_model[model_id] = {
                    "metrics": asdict(compute(scored, errors)),
                    "reliability": [asdict(b) for b in reliability(scored)],
                    "latency_p50_ms": p50,
                    "latency_p95_ms": p95,
                    "cost_per_1k_usd": cost_per_thousand(calls),
                }
            summary[task.name]["questions"][question.name] = per_model
    return summary


def _table(per_model: dict) -> list[str]:
    lines = [
        f"| Model | Accuracy | Avg confidence | Overconfidence (pts) | ECE | Answers ≥{CONFIDENT:.0%} confident | "
        f"…and right | Invalid | p50 / p95 ms | $ per 1k |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model_id, result in per_model.items():
        m = result["metrics"]
        cost = result["cost_per_1k_usd"]
        lines.append(
            f"| {model_id} | {_pct(m['accuracy'])} | {_pct(m['mean_confidence'])} | "
            f"{_pct(m['overconfidence'], signed=True)} | {_pct(m['ece'])} | {_pct(m['confident_share'])} | "
            f"{_pct(m['confident_accuracy'])} | {m['invalid']} | "
            f"{_ms(result['latency_p50_ms'])} / {_ms(result['latency_p95_ms'])} | "
            f"{f'${cost:.3f}' if cost is not None else '–'} |"
        )
        if m["errors"]:
            lines[-1] += f" ({m['errors']} failed calls excluded)"
    return lines


def write(
    tasks: list[Task], results: dict[str, dict[str, list[Call]]], out_dir: Path, *, jev_model: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(tasks, results)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    lines = [
        "# Benchmark: Jev vs LLMs",
        "",
        f"Generated {date.today().isoformat()} by `uv run triage-bench report`. Jev model: `{jev_model}`. "
        "All models called through OpenRouter. LLMs answered under a JSON schema listing the allowed options, "
        "at temperature 0, and stated their confidence (0–100) with each answer.",
        "",
        "- **Overconfidence:** average stated confidence minus accuracy, in percentage points. Positive means "
        "the model claims more than it delivers.",
        "- **ECE:** expected calibration error: the average gap between confidence and accuracy, after "
        "grouping answers by confidence. Lower is better.",
        f"- **Answers ≥{CONFIDENT:.0%} confident / …and right:** what an automated system would act on without "
        "review, and how often it would be right.",
        "- **Latency** is wall time per call from the benchmark machine through OpenRouter, including any "
        "rate-limit retries. **Cost** is what OpenRouter billed (for Jev: input tokens × its listed price).",
        "- One run per model. Jev's `confidence` field is used; its separate probability for the chosen "
        "option was slightly worse calibrated on every question.",
        "",
    ]
    for task in tasks:
        lines += [f"## {task.title}", "", f"Source: {task.source}.", ""]
        for question in task.questions:
            per_model = summary[task.name]["questions"][question.name]
            if not per_model:
                continue
            heading = f"{task.title.split(':')[0]}: {question.name}"
            if len(task.questions) > 1:
                lines += [f"### {question.name}", ""]
            chart = f"reliability-{task.name}-{question.name}.svg"
            (out_dir / chart).write_text(reliability_svg(f"Calibration, {heading}", per_model))
            lines += [f"![Calibration, {heading}]({chart})", "", *_table(per_model), ""]
    path = out_dir / "report.md"
    path.write_text("\n".join(lines))
    return path
