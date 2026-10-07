"""Triage the bundled sample tickets with the configured backend: `uv run triage-seed`."""

import asyncio
import json
from importlib.resources import files

from triage import service
from triage.config import Settings
from triage.db import Database
from triage.main import build_engine
from triage.schemas import TicketIn


def load_samples() -> list[TicketIn]:
    raw = json.loads(files("triage").joinpath("sample_tickets.json").read_text())
    return [TicketIn.model_validate(item) for item in raw]


async def seed(settings: Settings) -> None:
    db = Database(settings.database_url)
    await db.create_all()
    engine = build_engine(settings)
    try:
        async with db.sessions() as session:
            for ticket_in in load_samples():
                ticket = await service.triage(session, engine, ticket_in, audit_rate=settings.audit_rate)
                audit = " (audit)" if ticket.audit else ""
                print(
                    f"#{ticket.id:<4} {ticket.route:<7} {ticket.category or '-':<10} {ticket.subject}{audit}"
                )
    finally:
        await engine.aclose()
        await db.dispose()


def main() -> None:
    asyncio.run(seed(Settings()))


if __name__ == "__main__":
    main()
