"""Smoke test: call every endpoint of a running app and check the responses.

Usage: uv run python scripts/smoke.py [base-url]   (default: http://127.0.0.1:8000)

It creates one ticket, so point the app at a throwaway database first, for example:
  TRIAGE_DATABASE_URL=sqlite+aiosqlite:///./smoke.db uv run uvicorn triage.main:create_app --factory
"""

import json
import sys
import time
import urllib.error
import urllib.request

SAMPLE_TICKET = {
    "subject": "Charged twice",
    "body": "My credit card was charged twice for the subscription this month. Please refund the duplicate payment.",
}


def request(url: str, payload: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def main(base: str) -> int:
    failures = 0

    def expect(label: str, status: int, wanted: int, detail: str = "") -> None:
        nonlocal failures
        ok = status == wanted
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: HTTP {status}{f'  {detail}' if detail else ''}")

    for attempt in range(30):  # give a just-started app up to 15 s
        try:
            status, body = request(f"{base}/healthz")
            break
        except OSError as error:
            if attempt == 29:
                print(f"cannot reach {base}: {error}")
                return 1
            time.sleep(0.5)
    expect("healthz", status, 200, body)
    started = time.perf_counter()
    status, body = request(f"{base}/api/tickets", SAMPLE_TICKET)
    elapsed = time.perf_counter() - started
    ticket = json.loads(body) if status == 201 else {}
    summary = (
        f"route={ticket.get('route')} category={ticket.get('category')} "
        f"urgency={ticket.get('urgency_level')} in {elapsed:.2f}s"
    )
    expect("create ticket", status, 201, summary if ticket else body[:200])
    if ticket.get("decision", {}).get("error"):
        failures += 1
        print(f"  FAIL decision error: {ticket['decision']['error']}")
    status, body = request(f"{base}/api/stats")
    expect("stats", status, 200, body)
    for path in ("/", "/review", "/docs"):
        expect(path, request(f"{base}{path}")[0], 200)
    print("smoke test passed" if not failures else f"smoke test failed ({failures} problem(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
