"""Database plumbing (SQLAlchemy 2.x)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base


def _prepare_sqlite_path(url: str) -> None:
    if url.startswith("sqlite") and "///" in url:
        raw = url.split("///", 1)[1]
        if raw and not raw.startswith(":memory:"):
            path = Path(raw)
            if path.parent and str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)


def build_engine(url: str | None = None):
    database_url = url or settings.database_url
    _prepare_sqlite_path(database_url)
    kwargs: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 10
    return create_engine(database_url, **kwargs)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_schema(target_engine=None) -> None:
    Base.metadata.create_all(bind=target_engine or engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope(factory: sessionmaker | None = None) -> Iterator[Session]:
    session = (factory or SessionLocal)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
