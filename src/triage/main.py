"""App factory. Run with: uvicorn triage.main:create_app --factory"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from triage import api, web
from triage.config import Settings
from triage.costs import expected_mistake_cost
from triage.db import Database
from triage.decisions import (
    Backend,
    CostPolicy,
    DecisionEngine,
    RoutingPolicy,
    ThresholdPolicy,
    Thresholds,
    build_client,
)
from triage.questions import GATED


def build_engine(settings: Settings) -> DecisionEngine:
    if settings.backend is Backend.LAYA:
        api_key, base_url = settings.laya_api_key, settings.laya_base_url
    else:
        api_key, base_url = settings.typesafe_api_key, settings.jev_base_url
    client = build_client(
        settings.backend,
        api_key=api_key.get_secret_value() if api_key else None,
        base_url=base_url,
        model=settings.model,
        timeout=settings.request_timeout,
    )
    return DecisionEngine(client, build_policy(settings), backend=settings.backend)


def build_policy(settings: Settings) -> RoutingPolicy:
    if settings.routing == "threshold":
        thresholds = Thresholds(auto=settings.auto_threshold, review=settings.review_threshold)
        return ThresholdPolicy(thresholds, required=GATED)
    return CostPolicy(
        expected_mistake_cost,
        required=GATED,
        review_cost=settings.review_cost,
        escalate_cost=settings.escalate_cost,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings.database_url)
        await app.state.db.create_all()
        app.state.engine = build_engine(settings)
        try:
            yield
        finally:
            await app.state.engine.aclose()
            await app.state.db.dispose()

    app = FastAPI(
        title="Support Ticket Router",
        summary="Sorts customer support tickets automatically and sends the uncertain ones to a person.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api.router)
    app.include_router(api.health_router)
    app.include_router(web.router)
    return app
