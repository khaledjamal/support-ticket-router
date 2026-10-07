import asyncio
import sqlite3
from pathlib import Path

import pytest

from triage.db import Database


def create_all(path: Path) -> None:
    async def run() -> None:
        db = Database(f"sqlite+aiosqlite:///{path}")
        try:
            await db.create_all()
        finally:
            await db.dispose()

    asyncio.run(run())


def test_creates_a_new_database(tmp_path: Path) -> None:
    create_all(tmp_path / "new.db")
    create_all(tmp_path / "new.db")  # and accepts its own schema on the next start


def test_refuses_a_database_from_an_older_schema(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, subject TEXT)")

    with pytest.raises(
        RuntimeError,
        match=r"older version \(missing tickets\.created_at.*Delete it and run `uv run triage-seed`",
    ):
        create_all(path)
