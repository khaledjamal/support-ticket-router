"""An offline stand-in for the `/v1/systemone` endpoint, for demos and tests without an API key.

It scores each option by how many words it shares with the state, so tickets that mention an
option's keywords get a confident answer and vague ones get an uncertain one. It is a heuristic,
not a model: use it to exercise the app, never to judge Jev.
"""

import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

import httpx2

_WORD = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    (  # noqa: SIM905 - a word list reads better as one string
        "the and for this that with not are was you your our they their from have has can can't cannot "
        "about into been will would there here what when which who how its it's all any but just also "
        "please thanks thank hello help need needs"
    ).split()
)
_STEM = 5
"""Compare words by their first letters, a crude stemmer: charged/charges -> charg."""
_SHARPNESS = 2.0
"""Logit added per shared word: one shared word -> ~0.7 probability among four options, two -> ~0.95."""


def _words(value: Any) -> set[str]:
    text = json.dumps(value) if not isinstance(value, str) else value
    words = set()
    for word in _WORD.findall(text.lower()):
        if len(word) < 3 or word in _STOPWORDS:
            continue
        words.add(word[:_STEM])
    return words


def _softmax(logits: Iterable[float]) -> list[float]:
    exps = [math.exp(logit) for logit in logits]
    total = sum(exps)
    return [value / total for value in exps]


def _choice(text: set[str], criteria: Mapping[str, Any]) -> dict[str, Any]:
    labels = list(criteria)
    probs = _softmax(_SHARPNESS * len(text & _words([label, criteria[label]])) for label in labels)
    best = max(range(len(labels)), key=probs.__getitem__)
    return {
        "type": "choice",
        "choice": labels[best],
        "confidence": round(probs[best], 4),
        "probabilities": {label: round(p, 4) for label, p in zip(labels, probs, strict=True)},
    }


def _score(text: set[str], criteria: list[Any]) -> dict[str, Any]:
    probs = _softmax(_SHARPNESS * len(text & _words(level)) for level in criteria)
    return {
        "type": "score",
        "score": round(sum(level * p for level, p in enumerate(probs)), 4),
        "confidence": round(max(probs), 4),
        "legend": {str(level): description for level, description in enumerate(criteria)},
        "probabilities": {str(level): round(p, 4) for level, p in enumerate(probs)},
    }


def _noul(text: set[str], criteria: Mapping[str, Any] | None) -> dict[str, Any]:
    criteria = criteria or {}
    yes = len(text & _words(criteria.get("true") or ""))
    no = len(text & _words(criteria.get("false") or ""))
    return {"type": "noul", "noul": round(1 / (1 + math.exp(-_SHARPNESS * (yes - no))), 4)}


def answer(state: Any, questions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build a `/v1/systemone` response body for the given request."""
    text = _words(state)
    answers = {}
    for name, question in questions.items():
        match question["type"]:
            case "choice":
                answers[name] = _choice(text, question["criteria"])
            case "score":
                answers[name] = _score(text, question["criteria"])
            case "noul":
                answers[name] = _noul(text, question.get("criteria"))
    return {
        "model": "fake-jev",
        "answers": answers,
        "usage": {"input_tokens": len(json.dumps(state)) // 4, "output_tokens": 0},
    }


def handle(request: httpx2.Request) -> httpx2.Response:
    """`httpx2.MockTransport` handler serving the System One endpoint."""
    if request.method != "POST" or request.url.path != "/v1/systemone":
        return httpx2.Response(
            404, json={"error": f"fake backend does not serve {request.method} {request.url.path}"}
        )
    body = json.loads(request.content)
    return httpx2.Response(200, json=answer(body["state"], body["questions"]))
