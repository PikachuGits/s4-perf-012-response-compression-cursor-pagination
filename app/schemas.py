"""Pydantic response models and OpenAPI-friendly field descriptions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MemberOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    site_id: uuid.UUID
    display_name: str
    company_name: str
    industry: str
    tier: str
    status: str
    created_at: datetime
    created_by: uuid.UUID
    updated_at: datetime


class InteractionOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    site_id: uuid.UUID
    member_id: uuid.UUID
    channel: str
    direction: str
    subject: str
    summary: str
    occurred_at: datetime
    created_at: datetime
    created_by: uuid.UUID
    updated_at: datetime


class AuditLogOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    site_id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    detail: str
    created_at: datetime
    created_by: uuid.UUID
    updated_at: datetime


class PageBase(BaseModel):
    limit: int = Field(description="本页最多返回的记录数（1–200，默认 50）")
    has_more: bool = Field(description="是否还有下一页")
    pagination: Literal["cursor", "offset"] = Field(description="本次使用的分页方式")
    next_cursor: str | None = Field(
        default=None,
        description="下一页游标；has_more=false 时为 null。仅 pagination=cursor 返回。",
    )
    offset: int | None = Field(default=None, description="本次偏移量，仅 offset 模式返回")
    next_offset: int | None = Field(
        default=None, description="下一页偏移量；has_more=false 时为 null。仅 offset 模式返回"
    )
    total: int | None = Field(
        default=None,
        description="可选总数；仅 offset 模式且 with_total=1 时返回（会额外执行 COUNT）",
    )


class MemberPage(PageBase):
    items: list[MemberOut]


class InteractionPage(PageBase):
    items: list[InteractionOut]


class AuditLogPage(PageBase):
    items: list[AuditLogOut]


class DictionaryOut(BaseModel):
    """A deliberately small payload (< 1 KB) used to prove the threshold."""

    industries: list[str]
    tiers: list[str]
    statuses: list[str]
