"""Server-rendered dashboard and review page. HTMX avoids page reloads; without JavaScript the forms still work."""

from pathlib import Path
from typing import Annotated, get_args

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from triage import service
from triage.decisions import Route
from triage.deps import EngineDep, SessionDep, SettingsDep, TicketDep
from triage.schemas import Category, ReviewIn, TicketIn, UrgencyLevel

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


def _is_htmx(request: Request) -> bool:
    return "HX-Request" in request.headers


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: SessionDep, route: Route | None = None) -> Response:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": await service.stats(session),
            "tickets": await service.list_tickets(session, route=route),
            "route_filter": route,
            "routes": list(Route),
            "engine": request.app.state.engine,
        },
    )


@router.post("/ui/tickets")
async def submit_ticket(
    request: Request,
    ticket_in: Annotated[TicketIn, Form()],
    session: SessionDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> Response:
    ticket = await service.triage(session, engine, ticket_in, audit_rate=settings.audit_rate)
    if not _is_htmx(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "_ticket_row.html",
        {"ticket": ticket, "fresh": True},
        headers={"HX-Trigger": "ticket-created"},
    )


@router.get("/ui/stats", response_class=HTMLResponse)
async def stats_panel(request: Request, session: SessionDep) -> Response:
    return templates.TemplateResponse(request, "_stats.html", {"stats": await service.stats(session)})


@router.get("/review", response_class=HTMLResponse)
async def review_page(request: Request, session: SessionDep) -> Response:
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "tickets": await service.review_queue(session),
            "stats": await service.stats(session),
            "categories": get_args(Category),
            "urgency_levels": get_args(UrgencyLevel),
        },
    )


@router.post("/ui/tickets/{ticket_id}/review")
async def submit_review(
    request: Request, ticket: TicketDep, review_in: Annotated[ReviewIn, Form()], session: SessionDep
) -> Response:
    ticket = await service.review(session, ticket, review_in)
    if not _is_htmx(request):
        return RedirectResponse("/review", status_code=303)
    return templates.TemplateResponse(request, "_reviewed.html", {"ticket": ticket})
