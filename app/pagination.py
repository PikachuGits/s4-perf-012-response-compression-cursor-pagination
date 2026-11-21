"""Keyset ("cursor") and legacy offset pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from .cursor import (
    CursorError,
    decode_cursor,
    encode_cursor,
    normalize_ts,
    scope_fingerprint,
)


@dataclass
class Page:
    items: list
    has_more: bool
    next_cursor: str | None = None
    next_offset: int | None = None


def _bind_ts(db: Session, ts):
    """SQLite stores naive datetimes; PostgreSQL keeps the timezone."""
    if db.get_bind().dialect.name == "sqlite":
        return normalize_ts(ts).replace(tzinfo=None)
    return normalize_ts(ts)


def paginate_cursor(
    db: Session,
    stmt,
    model,
    sort_column,
    *,
    limit: int,
    cursor: str | None,
    resource: str,
    org_id,
    filters: dict[str, str | None],
    signing_key: str,
) -> Page:
    scope = scope_fingerprint(resource, org_id, filters)
    if cursor:
        key = decode_cursor(cursor, scope, signing_key)
        bound_ts = _bind_ts(db, key.ts)
        # Row comparison is the key to a true index range scan: PostgreSQL
        # pushes ``(created_at, id) < (:ts, :id)`` into the composite index,
        # while an equivalent OR expression would only be a filter and would
        # still scan all newer rows. SQLite (>= 3.15) supports row values too.
        stmt = stmt.where(tuple_(sort_column, model.id) < tuple_(bound_ts, key.id))
    stmt = stmt.order_by(sort_column.desc(), model.id.desc()).limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            getattr(last, sort_column.key), last.id, scope, signing_key
        )
    return Page(items=items, has_more=has_more, next_cursor=next_cursor)


def paginate_offset(
    db: Session,
    stmt,
    model,
    sort_column,
    *,
    limit: int,
    offset: int,
    count: bool = False,
) -> Page:
    stmt = stmt.order_by(sort_column.desc(), model.id.desc()).offset(offset).limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    return Page(
        items=items,
        has_more=has_more,
        next_offset=(offset + limit) if has_more else None,
    )


def count_rows(db: Session, stmt) -> int:
    """Count for the optional ``with_total=1`` compatibility field."""
    from sqlalchemy import func, select as _select

    scalar_stmt = _select(func.count()).select_from(stmt.order_by(None).limit(None).subquery())
    return int(db.execute(scalar_stmt).scalar_one())
