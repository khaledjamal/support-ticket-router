from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from triage.config import Settings
from triage.db import Ticket
from triage.decisions import DecisionEngine


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as session:
        yield session


def get_engine(request: Request) -> DecisionEngine:
    return request.app.state.engine


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[AsyncSession, Depends(get_session)]
EngineDep = Annotated[DecisionEngine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_ticket(ticket_id: int, session: SessionDep) -> Ticket:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"ticket {ticket_id} not found")
    return ticket


TicketDep = Annotated[Ticket, Depends(get_ticket)]
