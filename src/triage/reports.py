"""Read-only reports on a ticket database.

uv run triage-tickets [path.db]   the routing table: route, answers, confidence and latency per ticket
uv run triage-policies [path.db]  re-route the stored Jev answers under the threshold and cost policies,
                                  side by side. No API calls, so it's free and repeatable.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from pydantic import TypeAdapter
from typesafe_sdk import Answer

from triage.config import Settings
from triage.main import build_policy

DEFAULT_DB = "triage.db"


def _connect(db: str) -> sqlite3.Connection:
    path = Path(db)
    if not path.exists():
        sys.exit(f"no database at {path}; create one with `uv run triage-seed`")
    return sqlite3.connect(path)


def _db_argument(description: str) -> str:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("db", nargs="?", default=DEFAULT_DB, help=f"SQLite file (default: ./{DEFAULT_DB})")
    return parser.parse_args().db


def print_tickets(db: str) -> None:
    rows = _connect(db).execute(
        """SELECT t.id, t.route, t.category, t.category_confidence, t.urgency_level, t.urgency_confidence,
                  t.churn_probability, d.latency_ms, t.subject, t.route_reason
           FROM tickets t JOIN decisions d ON d.ticket_id = t.id ORDER BY t.id"""
    )
    print(
        f"{'#':>3} {'route':<6} {'category':<10} {'conf':>4}  {'urgency':<8} {'conf':>4}  {'churn':>5} {'ms':>5}  subject / reason"
    )

    def num(value: float | None, fmt: str) -> str:
        return format(value, fmt) if value is not None else "-"

    for id_, route, cat, cat_c, urg, urg_c, churn, ms, subject, reason in rows:
        print(
            f"{id_:>3} {route:<6} {cat or '-':<10} {num(cat_c, '.2f'):>4}  {urg or '-':<8} {num(urg_c, '.2f'):>4}  "
            f"{num(churn, '.2f'):>5} {ms:>5.0f}  {subject} / {reason}"
        )


def print_policies(db: str) -> None:
    connection = _connect(db)
    settings = Settings()
    compared = {
        name: build_policy(settings.model_copy(update={"routing": name})) for name in ("threshold", "cost")
    }
    adapter = TypeAdapter(dict[str, Answer])
    rows = connection.execute(
        """SELECT t.id, t.subject, d.answers FROM tickets t JOIN decisions d ON d.ticket_id = t.id
           WHERE d.error IS NULL ORDER BY t.id"""
    )
    for policy in compared.values():
        print(f"  {policy.name:<9} = {policy.describe()}")
    print(f"\n{'#':>3} {'threshold':<9} {'cost':<6} {'exp. cost':>9}  subject / cost reason")
    totals: dict[str, Counter] = {name: Counter() for name in compared}
    for id_, subject, raw in rows:
        decisions = {name: policy.route(adapter.validate_json(raw)) for name, policy in compared.items()}
        for name, decision in decisions.items():
            totals[name][decision.route] += 1
        cost = decisions["cost"]
        changed = " *" if cost.route != decisions["threshold"].route else ""
        print(
            f"{id_:>3} {decisions['threshold'].route:<9} {cost.route:<6} ${cost.expected_cost or 0:>8.2f}  "
            f"{subject} / {cost.reason}{changed}"
        )
    print("\n(* = the policies disagree)")
    for name, counts in totals.items():
        print(
            f"  {name:<9} " + "  ".join(f"{route}={counts[route]}" for route in ("auto", "review", "human"))
        )


def tickets_main() -> None:
    print_tickets(_db_argument("Print the routing table for a ticket database."))


def policies_main() -> None:
    print_policies(
        _db_argument("Re-route stored answers under the threshold and cost policies, side by side.")
    )
