from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from triage import service
from triage.decisions import Route
from triage.deps import EngineDep, SessionDep, SettingsDep, TicketDep
from triage.schemas import ReviewIn, Stats, TicketIn, TicketOut

router = APIRouter(prefix="/api", tags=["tickets"])


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket(
    ticket_in: TicketIn, session: SessionDep, engine: EngineDep, settings: SettingsDep
) -> TicketOut:
    """Triage a ticket. Always stores it: if Jev is unreachable the ticket is routed to a human."""
    ticket = await service.triage(session, engine, ticket_in, audit_rate=settings.audit_rate)
    return TicketOut.model_validate(ticket)


@router.get("/tickets")
async def list_tickets(
    session: SessionDep,
    route: Route | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TicketOut]:
    return [
        TicketOut.model_validate(t) for t in await service.list_tickets(session, route=route, limit=limit)
    ]


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket: TicketDep) -> TicketOut:
    return TicketOut.model_validate(ticket)


@router.get("/review-queue", tags=["review"])
async def review_queue(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[TicketOut]:
    """REVIEW and HUMAN tickets, plus audited AUTO tickets, that nobody has reviewed yet. Oldest first."""
    return [TicketOut.model_validate(t) for t in await service.review_queue(session, limit=limit)]


@router.post("/tickets/{ticket_id}/review", tags=["review"])
async def review_ticket(ticket: TicketDep, review_in: ReviewIn, session: SessionDep) -> TicketOut:
    """Record the correct category and urgency. Any ticket can be reviewed; a new review replaces the old one."""
    return TicketOut.model_validate(await service.review(session, ticket, review_in))


@router.get("/stats")
async def get_stats(session: SessionDep) -> Stats:
    return await service.stats(session)


health_router = APIRouter(tags=["health"])


@health_router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    return {"status": "ok", "backend": request.app.state.engine.backend}
