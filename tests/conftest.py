"""Shared fixtures: one isolated SQLite file, 12k synthetic rows per list."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = ROOT / ".data" / "test-perf012.sqlite3"
DEFAULT_URL = f"sqlite+pysqlite:///{TEST_DB}"
TEST_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)


def _assert_isolated_pg(url: str) -> None:
    """Refuse to run destructive tests against a shared PostgreSQL database."""
    if not url.startswith("postgresql"):
        return
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    host = parsed.host or ""
    database = parsed.database or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("TEST_DATABASE_URL 必须显式指向本机（127.0.0.1/localhost/::1）")
    if not any(token in database.lower() for token in ("test", "bench", "perf012")):
        raise SystemExit("TEST_DATABASE_URL 的库名必须包含 test/bench/perf012")


_assert_isolated_pg(TEST_URL)
if TEST_URL == DEFAULT_URL and TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = TEST_URL
os.environ["AUTO_CREATE_SCHEMA"] = "0"
os.environ["SEED_DEMO_DATA"] = "0"
os.environ["CURSOR_SIGNING_KEY"] = "test-only-cursor-signing-key"
os.environ["ENABLE_OFFSET_BASELINE"] = "1"
os.environ["COMPRESSION_MIN_BYTES"] = "1024"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.constants import (  # noqa: E402
    DEMO_ORG,
    DEMO_SITE,
    DEMO_USER,
    SECOND_ORG,
    SECOND_SITE,
    SECOND_USER,
)
from app.db import init_schema, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_dataset  # noqa: E402

MEMBERS = 12_000
INTERACTIONS = 12_000
AUDITS = 12_000


@pytest.fixture(scope="session", autouse=True)
def database():
    init_schema()
    with session_scope() as db:
        seed_dataset(
            db,
            members=MEMBERS,
            interactions=INTERACTIONS,
            audits=AUDITS,
        )
        seed_dataset(
            db,
            org_id=SECOND_ORG,
            site_id=SECOND_SITE,
            user_id=SECOND_USER,
            members=120,
            interactions=120,
            audits=120,
            start_index=800000,
        )
    yield
    if TEST_URL == DEFAULT_URL:
        from sqlalchemy import text

        with session_scope() as db:
            db.execute(text("DELETE FROM members"))
            db.execute(text("DELETE FROM interactions"))
            db.execute(text("DELETE FROM audit_logs"))


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def headers():
    return {"X-Org-Id": str(DEMO_ORG)}
