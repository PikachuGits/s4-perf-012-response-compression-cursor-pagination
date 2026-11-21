"""Deterministic, entirely synthetic seed data.

No real person, company or customer name is used. Names are explicitly
labelled ``虚构`` / ``样例`` so an accidental screenshot cannot be mistaken for
production data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .constants import DEMO_ORG, DEMO_SITE, DEMO_USER, SECOND_ORG, SECOND_SITE, SECOND_USER
from .ids import uuid7_for_datetime
from .models import AuditLog, Interaction, Member

BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

INDUSTRIES = ["智能制造", "商贸物流", "建筑设计", "文化传媒", "信息技术", "生命健康"]
TIERS = ["platinum", "gold", "silver", "bronze"]
STATUSES = ["active", "active", "active", "inactive", "pending"]
CHANNELS = ["wechat", "phone", "email", "meeting"]
ACTIONS = ["member.create", "member.update", "member.login", "report.export", "role.change"]


def _member_rows(count: int, org_id: uuid.UUID, site_id: uuid.UUID, user_id: uuid.UUID, start_index: int = 0):
    for offset in range(count):
        i = start_index + offset
        ts = BASE_TIME + timedelta(seconds=i * 3)
        yield {
            "id": uuid7_for_datetime(ts),
            "org_id": org_id,
            "site_id": site_id,
            "display_name": f"样例会员 {i:06d}",
            "company_name": f"虚构企业 {i:06d} 有限公司",
            "industry": INDUSTRIES[i % len(INDUSTRIES)],
            "tier": TIERS[i % len(TIERS)],
            "status": STATUSES[i % len(STATUSES)],
            "created_at": ts,
            "created_by": user_id,
            "updated_at": ts,
            "deleted_at": None,
        }


def _interaction_rows(count: int, org_id: uuid.UUID, site_id: uuid.UUID, user_id: uuid.UUID, start_index: int = 0):
    for offset in range(count):
        i = start_index + offset
        occurred = BASE_TIME + timedelta(seconds=i * 2)
        yield {
            "id": uuid7_for_datetime(occurred),
            "org_id": org_id,
            "site_id": site_id,
            "member_id": uuid7_for_datetime(BASE_TIME + timedelta(seconds=(i % 1000) * 3)),
            "channel": CHANNELS[i % len(CHANNELS)],
            "direction": "outbound" if i % 3 else "inbound",
            "subject": f"样例沟通主题 {i:06d}：会员权益与空间使用确认",
            "summary": (
                f"这是第 {i:06d} 条虚构互动记录，用于验证深分页性能。"
                "内容包含需求确认、场地预约、活动邀约与后续跟进安排，不涉及任何真实个人或企业信息。"
            ),
            "occurred_at": occurred,
            "created_at": occurred,
            "created_by": user_id,
            "updated_at": occurred,
            "deleted_at": None,
        }


def _audit_rows(count: int, org_id: uuid.UUID, site_id: uuid.UUID, user_id: uuid.UUID, start_index: int = 0):
    for offset in range(count):
        i = start_index + offset
        ts = BASE_TIME + timedelta(seconds=i)
        yield {
            "id": uuid7_for_datetime(ts),
            "org_id": org_id,
            "site_id": site_id,
            "actor_id": user_id,
            "action": ACTIONS[i % len(ACTIONS)],
            "resource_type": ["member", "interaction", "report", "role"][i % 4],
            "resource_id": str(uuid7_for_datetime(ts)),
            "outcome": "success" if i % 11 else "denied",
            "detail": (
                f"虚构审计事件 {i:06d}：操作者执行了受控操作，"
                "系统记录了请求来源、对象标识与结果，用于审计查询分页验证。"
            ),
            "created_at": ts,
            "created_by": user_id,
            "updated_at": ts,
            "deleted_at": None,
        }


def seed_dataset(
    db: Session,
    *,
    org_id: uuid.UUID = DEMO_ORG,
    site_id: uuid.UUID = DEMO_SITE,
    user_id: uuid.UUID = DEMO_USER,
    members: int = 0,
    interactions: int = 0,
    audits: int = 0,
    start_index: int = 0,
    batch_size: int = 5000,
) -> dict[str, int]:
    """Insert synthetic rows in batches. Returns inserted counts."""
    inserted = {"members": 0, "interactions": 0, "audits": 0}
    for model, generator, key, total in (
        (Member, _member_rows, "members", members),
        (Interaction, _interaction_rows, "interactions", interactions),
        (AuditLog, _audit_rows, "audits", audits),
    ):
        batch: list[dict] = []
        for row in generator(total, org_id, site_id, user_id, start_index):
            batch.append(row)
            if len(batch) >= batch_size:
                db.bulk_insert_mappings(model, batch)
                inserted[key] += len(batch)
                batch.clear()
        if batch:
            db.bulk_insert_mappings(model, batch)
            inserted[key] += len(batch)
    db.commit()
    return inserted


def seed_demo(db: Session) -> dict[str, int]:
    """Small demo data set used when SEED_DEMO_DATA=1."""
    if db.execute(select(func.count()).select_from(Member)).scalar_one() > 0:
        return {"members": 0, "interactions": 0, "audits": 0}
    counts = seed_dataset(db, members=1200, interactions=1200, audits=1200)
    seed_dataset(
        db,
        org_id=SECOND_ORG,
        site_id=SECOND_SITE,
        user_id=SECOND_USER,
        members=80,
        interactions=80,
        audits=80,
        start_index=900000,
    )
    return counts
