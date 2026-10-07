from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Connection,
    DateTime,
    Dialect,
    ForeignKey,
    String,
    Text,
    TypeDecorator,
    inspect,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC and always load timezone-aware values; SQLite would otherwise hand back naive datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime; pass a timezone-aware value")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    customer: Mapped[str | None] = mapped_column(String(200))

    category: Mapped[str | None] = mapped_column(String(50), index=True)
    category_confidence: Mapped[float | None]
    urgency_score: Mapped[float | None]
    urgency_level: Mapped[str | None] = mapped_column(String(20))
    urgency_confidence: Mapped[float | None]
    churn_probability: Mapped[float | None]
    at_risk: Mapped[bool] = mapped_column(default=False)

    route: Mapped[str] = mapped_column(String(10), index=True)
    route_reason: Mapped[str] = mapped_column(String(200))
    expected_cost: Mapped[float | None]
    """Cost routing only: expected cost of acting on the answers without review."""
    audit: Mapped[bool] = mapped_column(default=False)
    """An AUTO ticket sampled for human review, so automated accuracy can be measured without bias."""

    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    reviewed_category: Mapped[str | None] = mapped_column(String(50))
    reviewed_urgency_level: Mapped[str | None] = mapped_column(String(20))

    decision: Mapped["Decision"] = relationship(
        back_populates="ticket", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def needs_review(self) -> bool:
        return self.reviewed_at is None and (self.route != "auto" or self.audit)


class Decision(Base):
    """One raw Jev call per ticket: the record that later calibration checks are built from."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    backend: Mapped[str] = mapped_column(String(10))
    policy: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[float]
    input_tokens: Mapped[int | None]
    answers: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    ticket: Mapped[Ticket] = relationship(back_populates="decision")


def _missing_columns(conn: Connection) -> list[str]:
    inspector = inspect(conn)
    return [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name not in {c["name"] for c in inspector.get_columns(table.name)}
    ]


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        """Create missing tables, and refuse to start on tables from an older version of the schema.

        There are no migrations yet, so an outdated database would otherwise fail later with an obscure SQL error.
        """
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            missing = await conn.run_sync(_missing_columns)
        if missing:
            raise RuntimeError(
                f"database {self.engine.url.database} is from an older version (missing {', '.join(missing)}). "
                "Delete it and run `uv run triage-seed` to recreate it."
            )

    async def dispose(self) -> None:
        await self.engine.dispose()
