import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from parti.app import create_app
from parti.config import Settings
from parti.db import Base, make_session_factory
from parti.seed import PROFESSOR_EMAIL, seed

TEST_DB = os.environ.get("PARTI_TEST_DATABASE_URL", "postgresql+psycopg://parti@127.0.0.1:5432/parti_test")
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrated() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = TEST_DB
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture()
def db() -> Iterator[Session]:
    factory = make_session_factory(TEST_DB)
    with factory() as session:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
        seed(session)
        yield session


def make_client(email: str | None = PROFESSOR_EMAIL, **settings: Any) -> TestClient:
    return TestClient(
        create_app(
            Settings(_env_file=None, database_url=TEST_DB, demo_user_email=email, env="test", **settings)
        )
    )


@pytest.fixture()
def client(db: Session) -> TestClient:
    return make_client()
