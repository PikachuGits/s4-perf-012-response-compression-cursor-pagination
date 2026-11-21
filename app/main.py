"""FastAPI reference service for S4-PERF-012.

Three deep-paginated list endpoints are provided in two modes:

* ``pagination=cursor`` (default, new): signed keyset pagination;
* ``pagination=offset`` (deprecated, kept only for the before/after benchmark
  and a short rollout window): the original LIMIT/OFFSET query.

Compression is applied by :class:`app.compression.JsonCompressionMiddleware`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import seed
from .compression import JsonCompressionMiddleware
from .config import settings
from .cursor import CursorError
from .db import get_db, init_schema, session_scope
from .identity import Identity, identity
from .models import AuditLog, Interaction, Member
from .pagination import count_rows, paginate_cursor, paginate_offset
from .schemas import (
    AuditLogPage,
    DictionaryOut,
    InteractionPage,
    MemberPage,
)

RESOURCE_MEMBERS = "members"
RESOURCE_INTERACTIONS = "interactions"
RESOURCE_AUDIT_LOGS = "audit-logs"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_schema:
        init_schema()
    if settings.seed_demo_data:
        with session_scope() as db:
            seed.seed_demo(db)
    yield


app = FastAPI(
    title="S4-PERF-012 · 接口响应压缩与游标分页参考实现",
    version="1.0.0",
    description=(
        "会员空间平台性能工单的独立参考实现。列表接口默认使用签名游标分页；"
        "`pagination=offset` 仅为前后对比保留，已标记 deprecated。"
        "大于 1 KB 的 JSON 响应按 `Accept-Encoding` 协商 br/gzip。"
    ),
    lifespan=lifespan,
)
app.add_middleware(
    JsonCompressionMiddleware,
    minimum_size=settings.compression_min_bytes,
    enable_brotli=settings.compression_enable_brotli,
)


@app.exception_handler(CursorError)
async def cursor_error_handler(_: Request, exc: CursorError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _check_mode(pagination: str, cursor: str | None, offset: int) -> None:
    if pagination == "offset":
        if not settings.enable_offset_baseline:
            raise _bad_request(
                "OFFSET_PAGINATION_DISABLED",
                "offset 分页已下线，请使用 pagination=cursor 与 next_cursor",
            )
        if cursor:
            raise _bad_request("CURSOR_NOT_ALLOWED", "offset 模式不接受 cursor 参数")
        return
    if offset:
        raise _bad_request(
            "OFFSET_NOT_ALLOWED",
            "cursor 模式不接受 offset 参数；请从第一页开始并使用 next_cursor",
        )


def _page_payload(
    page, *, limit: int, pagination: str, offset: int | None = None, total: int | None = None
):
    payload = {
        "items": page.items,
        "limit": limit,
        "has_more": page.has_more,
        "pagination": pagination,
        "next_cursor": page.next_cursor,
        "offset": None,
        "next_offset": None,
        "total": None,
    }
    if pagination == "offset":
        payload["offset"] = offset
        payload["next_offset"] = page.next_offset
        payload["total"] = total
    return payload


LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=200,
        description="每页记录数，1–200，默认 50。游标分页同样适用。",
    ),
]
CursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "上一页返回的 next_cursor。首屏不传；必须原样回传，"
            "游标绑定了租户与筛选条件，跨条件复用会返回 CURSOR_SCOPE_MISMATCH。"
        ),
        examples=["eyJ2IjoxLCJzIjoi...0Y2Fi"],
    ),
]
PaginationQuery = Annotated[
    Literal["cursor", "offset"],
    Query(
        description=(
            "分页方式。默认 cursor（keyset，推荐）；offset 已废弃，"
            "仅用于灰度期与性能对比，后续将移除。"
        ),
    ),
]
OffsetQuery = Annotated[
    int,
    Query(
        ge=0,
        le=10_000_000,
        deprecated=True,
        description="仅 pagination=offset 时生效的旧偏移量；已废弃，新客户端不要使用。",
    ),
]
WithTotalQuery = Annotated[
    bool,
    Query(
        description="仅 offset 模式生效：是否额外执行 COUNT 返回 total（会增加响应时间）。",
    ),
]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/v1/meta/dictionaries",
    response_model=DictionaryOut,
    tags=["meta"],
    summary="小体积 JSON（低于 1 KB 压缩阈值）",
)
def dictionaries() -> DictionaryOut:
    return DictionaryOut(
        industries=seed.INDUSTRIES,
        tiers=seed.TIERS,
        statuses=sorted(set(seed.STATUSES)),
    )


@app.get(
    "/api/v1/members",
    response_model=MemberPage,
    tags=["members"],
    summary="会员列表（游标分页）",
    description=(
        "默认 pagination=cursor：首屏不传 cursor，后续把上一页的 next_cursor 原样回传。"
        "pagination=offset 为已废弃的兼容模式，仅用于灰度与性能对比。"
    ),
)
def list_members(
    ident: Identity = Depends(identity),
    db: Session = Depends(get_db),
    limit: LimitQuery = 50,
    cursor: CursorQuery = None,
    pagination: PaginationQuery = "cursor",
    offset: OffsetQuery = 0,
    with_total: WithTotalQuery = False,
    status_filter: Annotated[
        str | None, Query(alias="status", max_length=20, description="按会员状态筛选")
    ] = None,
    tier: Annotated[str | None, Query(max_length=20, description="按会员等级筛选")] = None,
    q: Annotated[
        str | None,
        Query(max_length=100, description="按会员名称或企业名称模糊搜索（虚构数据）"),
    ] = None,
) -> MemberPage:
    _check_mode(pagination, cursor, offset)
    stmt = select(Member).where(Member.org_id == ident.org_id, Member.deleted_at.is_(None))
    filters = {"status": status_filter, "tier": tier, "q": q}
    if status_filter:
        stmt = stmt.where(Member.status == status_filter)
    if tier:
        stmt = stmt.where(Member.tier == tier)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Member.display_name.ilike(like), Member.company_name.ilike(like)))

    if pagination == "cursor":
        page = paginate_cursor(
            db,
            stmt,
            Member,
            Member.created_at,
            limit=limit,
            cursor=cursor,
            resource=RESOURCE_MEMBERS,
            org_id=ident.org_id,
            filters=filters,
            signing_key=settings.cursor_signing_key,
        )
        return MemberPage(**_page_payload(page, limit=limit, pagination="cursor"))

    page = paginate_offset(db, stmt, Member, Member.created_at, limit=limit, offset=offset)
    total = count_rows(db, stmt) if with_total else None
    return MemberPage(**_page_payload(page, limit=limit, pagination="offset", offset=offset, total=total))


@app.get(
    "/api/v1/interactions",
    response_model=InteractionPage,
    tags=["interactions"],
    summary="互动记录列表（游标分页）",
    description=(
        "默认 pagination=cursor：首屏不传 cursor，后续把上一页的 next_cursor 原样回传。"
        "游标绑定 member_id/channel 筛选条件，跨条件复用返回 CURSOR_SCOPE_MISMATCH。"
    ),
)
def list_interactions(
    ident: Identity = Depends(identity),
    db: Session = Depends(get_db),
    limit: LimitQuery = 50,
    cursor: CursorQuery = None,
    pagination: PaginationQuery = "cursor",
    offset: OffsetQuery = 0,
    with_total: WithTotalQuery = False,
    member_id: Annotated[
        uuid.UUID | None, Query(description="按会员 ID 筛选，仅返回该会员的互动记录")
    ] = None,
    channel: Annotated[str | None, Query(max_length=30, description="按渠道筛选")] = None,
) -> InteractionPage:
    _check_mode(pagination, cursor, offset)
    stmt = select(Interaction).where(
        Interaction.org_id == ident.org_id, Interaction.deleted_at.is_(None)
    )
    filters = {"member_id": str(member_id) if member_id else None, "channel": channel}
    if member_id:
        stmt = stmt.where(Interaction.member_id == member_id)
    if channel:
        stmt = stmt.where(Interaction.channel == channel)

    if pagination == "cursor":
        page = paginate_cursor(
            db,
            stmt,
            Interaction,
            Interaction.occurred_at,
            limit=limit,
            cursor=cursor,
            resource=RESOURCE_INTERACTIONS,
            org_id=ident.org_id,
            filters=filters,
            signing_key=settings.cursor_signing_key,
        )
        return InteractionPage(**_page_payload(page, limit=limit, pagination="cursor"))

    page = paginate_offset(
        db, stmt, Interaction, Interaction.occurred_at, limit=limit, offset=offset
    )
    total = count_rows(db, stmt) if with_total else None
    return InteractionPage(
        **_page_payload(page, limit=limit, pagination="offset", offset=offset, total=total)
    )


@app.get(
    "/api/v1/audit-logs",
    response_model=AuditLogPage,
    tags=["audit"],
    summary="审计查询列表（游标分页）",
    description=(
        "默认 pagination=cursor：首屏不传 cursor，后续把上一页的 next_cursor 原样回传。"
        "游标绑定 action/actor_id/outcome 筛选条件，跨条件复用返回 CURSOR_SCOPE_MISMATCH。"
    ),
)
def list_audit_logs(
    ident: Identity = Depends(identity),
    db: Session = Depends(get_db),
    limit: LimitQuery = 50,
    cursor: CursorQuery = None,
    pagination: PaginationQuery = "cursor",
    offset: OffsetQuery = 0,
    with_total: WithTotalQuery = False,
    action: Annotated[str | None, Query(max_length=60, description="按操作类型筛选")] = None,
    actor_id: Annotated[
        uuid.UUID | None, Query(description="按操作者 ID 筛选")
    ] = None,
    outcome: Annotated[str | None, Query(max_length=20, description="按结果筛选")] = None,
) -> AuditLogPage:
    _check_mode(pagination, cursor, offset)
    stmt = select(AuditLog).where(
        AuditLog.org_id == ident.org_id, AuditLog.deleted_at.is_(None)
    )
    filters = {
        "action": action,
        "actor_id": str(actor_id) if actor_id else None,
        "outcome": outcome,
    }
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)

    if pagination == "cursor":
        page = paginate_cursor(
            db,
            stmt,
            AuditLog,
            AuditLog.created_at,
            limit=limit,
            cursor=cursor,
            resource=RESOURCE_AUDIT_LOGS,
            org_id=ident.org_id,
            filters=filters,
            signing_key=settings.cursor_signing_key,
        )
        return AuditLogPage(**_page_payload(page, limit=limit, pagination="cursor"))

    page = paginate_offset(
        db, stmt, AuditLog, AuditLog.created_at, limit=limit, offset=offset
    )
    total = count_rows(db, stmt) if with_total else None
    return AuditLogPage(
        **_page_payload(page, limit=limit, pagination="offset", offset=offset, total=total)
    )
