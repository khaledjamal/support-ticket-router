"""Reliability diagrams as standalone SVG: one small panel per model, stated confidence against accuracy.

Points on the dashed diagonal are perfectly calibrated; points below it mean the model was right less often
than it claimed. Dot area shows how many answers fell in that confidence range. Colors follow the light or
dark scheme of whatever shows the image.
"""

from html import escape
from math import sqrt

DISPLAY_NAMES = {
    "typesafe/jev-1.13": "Jev 1.13",
    "openai/gpt-4.1-mini": "GPT-4.1 mini",
    "anthropic/claude-haiku-4.5": "Claude Haiku 4.5",
    "google/gemini-2.5-flash": "Gemini 2.5 Flash",
}

PANEL = 168
"""Plot square, px."""
GAP = 56
LEFT, TOP, BOTTOM = 44, 80, 62
SPARSE = 5
"""Bins with fewer answers are drawn hollow and left out of the line: one answer is not a trend."""

STYLE = """
  .surface { fill: #fcfcfb; }
  .title { fill: #0b0b0b; font: 600 13px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .sub, .tick { fill: #52514e; font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .tick { font-variant-numeric: tabular-nums; }
  .axis-label { fill: #898781; font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; }
  .grid { stroke: #e1e0d9; stroke-width: 1; }
  .frame { stroke: #c3c2b7; stroke-width: 1; fill: none; }
  .ideal { stroke: #898781; stroke-width: 1.5; stroke-dasharray: 4 4; }
  .series { stroke: #2a78d6; stroke-width: 2; fill: none; }
  .dot { fill: #2a78d6; stroke: #fcfcfb; stroke-width: 2; }
  .dot.sparse { fill: #fcfcfb; stroke: #2a78d6; stroke-width: 1.5; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .title { fill: #ffffff; }
    .sub, .tick { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; }
    .frame { stroke: #383835; }
    .series { stroke: #3987e5; }
    .dot { fill: #3987e5; stroke: #1a1a19; }
    .dot.sparse { fill: #1a1a19; stroke: #3987e5; }
  }
"""


def _name(model_id: str) -> str:
    return DISPLAY_NAMES.get(model_id, model_id)


def reliability_svg(title: str, per_model: dict[str, dict]) -> str:
    """`per_model` maps model id -> summary entry with "metrics" and "reliability" (see report.summarize)."""
    models = list(per_model)
    width = LEFT + len(models) * PANEL + (len(models) - 1) * GAP + 16
    height = TOP + PANEL + BOTTOM
    max_count = max((b["count"] for m in per_model.values() for b in m["reliability"]), default=1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        f"<style>{STYLE}</style>",
        f'<rect class="surface" width="{width}" height="{height}" rx="8"/>',
        f'<text class="title" x="16" y="24">{escape(title)}</text>',
        '<text class="sub" x="16" y="42">Dashed line: perfectly calibrated. Below it: right less often '
        "than claimed.</text>",
        f'<text class="sub" x="16" y="57">Dot area: number of answers; hollow dots: fewer than {SPARSE}.</text>',
    ]
    for index, model_id in enumerate(models):
        entry = per_model[model_id]
        metrics = entry["metrics"]
        x0 = LEFT + index * (PANEL + GAP)
        y0 = TOP + 18

        def px(confidence: float, x0: float = x0) -> float:
            return x0 + confidence * PANEL

        def py(accuracy: float, y0: float = y0) -> float:
            return y0 + (1 - accuracy) * PANEL

        ece = metrics["ece"]
        parts.append(f'<text class="title" x="{x0}" y="{y0 - 22}">{escape(_name(model_id))}</text>')
        parts.append(
            f'<text class="sub" x="{x0}" y="{y0 - 7}">accuracy {100 * metrics["accuracy"]:.0f}% · '
            f"ECE {100 * ece:.0f}%</text>"
            if ece is not None
            else ""
        )
        for tick in (0.5,):
            parts.append(f'<line class="grid" x1="{px(tick)}" y1="{y0}" x2="{px(tick)}" y2="{y0 + PANEL}"/>')
            parts.append(f'<line class="grid" x1="{x0}" y1="{py(tick)}" x2="{x0 + PANEL}" y2="{py(tick)}"/>')
        parts.append(f'<rect class="frame" x="{x0}" y="{y0}" width="{PANEL}" height="{PANEL}"/>')
        parts.append(f'<line class="ideal" x1="{px(0)}" y1="{py(0)}" x2="{px(1)}" y2="{py(1)}"/>')
        for tick in (0, 0.5, 1):
            parts.append(
                f'<text class="tick" x="{px(tick)}" y="{y0 + PANEL + 14}" text-anchor="middle">'
                f"{100 * tick:.0f}%</text>"
            )
            if index == 0:
                parts.append(
                    f'<text class="tick" x="{x0 - 6}" y="{py(tick) + 4}" text-anchor="end">'
                    f"{100 * tick:.0f}%</text>"
                )
        parts.append(
            f'<text class="axis-label" x="{px(0.5)}" y="{y0 + PANEL + 30}" text-anchor="middle">'
            "stated confidence</text>"
        )
        if index == 0:
            cx, cy = x0 - 34, py(0.5)
            parts.append(
                f'<text class="axis-label" x="{cx}" y="{cy}" text-anchor="middle" '
                f'transform="rotate(-90 {cx} {cy})">accuracy</text>'
            )

        bins = entry["reliability"]
        dense = [b for b in bins if b["count"] >= SPARSE]
        if len(dense) > 1:
            points = " ".join(f"{px(b['mean_confidence']):.1f},{py(b['accuracy']):.1f}" for b in dense)
            parts.append(f'<polyline class="series" points="{points}"/>')
        for b in bins:
            sparse = " sparse" if b["count"] < SPARSE else ""
            radius = 4 + 6 * sqrt(b["count"] / max_count)
            tip = (
                f"{_name(model_id)}: {b['count']} answers stated {100 * b['low']:.0f}–{100 * b['high']:.0f}% "
                f"confidence (mean {100 * b['mean_confidence']:.0f}%) and were right "
                f"{100 * b['accuracy']:.0f}% of the time"
            )
            parts.append(
                f'<circle class="dot{sparse}" cx="{px(b["mean_confidence"]):.1f}" cy="{py(b["accuracy"]):.1f}" '
                f'r="{radius:.1f}"><title>{escape(tip)}</title></circle>'
            )
    parts.append("</svg>")
    return "\n".join(part for part in parts if part)
