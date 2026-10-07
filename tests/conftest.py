from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from triage.config import Settings
from triage.decisions import Backend
from triage.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        backend=Backend.FAKE,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        audit_rate=0.0,
        _env_file=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client
