#!/usr/bin/env python
"""Seed synthetic rows for the deep-pagination benchmark.

Only databases whose URL contains ``bench``/``test``/``perf012`` (or the local
``.data/`` SQLite file) may be reset, so a mistaken ``DATABASE_URL`` cannot
wipe a shared database.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from app.db import build_engine, sessionmaker  # noqa: E402
from app.models import Base, Member  # noqa: E402
from app.seed import seed_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--members", type=int, default=40_000)
    parser.add_argument("--interactions", type=int, default=80_000)
    parser.add_argument("--audits", type=int, default=120_000)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL", "sqlite+pysqlite:///./.data/bench.sqlite3")
    safe = any(token in url.lower() for token in ("bench", "test", "perf012", ".data/"))
    if args.reset and not safe:
        raise SystemExit(
            "拒绝重置：DATABASE_URL 不含 bench/test/perf012 标识，"
            "为防止误删共享库已中止。"
        )

    engine = build_engine(url)
    if args.reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.connect() as conn:
        dialect = engine.dialect.name
        if dialect == "postgresql":
            version = conn.execute(text("SHOW server_version")).scalar_one()
        else:
            version = conn.execute(text("SELECT sqlite_version()")).scalar_one()
        print(f"database: {dialect} {version}")

    with Session() as db:
        existing = db.execute(select(Member)).first()
        if existing and not args.reset:
            raise SystemExit("数据库已有数据；如需重建请加 --reset（仅限隔离库）")
        counts = seed_dataset(
            db,
            members=args.members,
            interactions=args.interactions,
            audits=args.audits,
        )
    print(f"seeded: {counts}")


if __name__ == "__main__":
    main()
