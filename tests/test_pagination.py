from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.constants import DEMO_ORG, DEMO_SITE, DEMO_USER, SECOND_ORG
from app.cursor import encode_cursor, scope_fingerprint
from app.db import session_scope
from app.models import AuditLog, Interaction, Member
from app.seed import BASE_TIME

PAGE = 200
LIMIT = 50
PROJECTED_OFFSET = (PAGE - 1) * LIMIT


def _cursor_from_previous_page(db, model, sort_column, resource: str, page: int = PAGE, limit: int = LIMIT):
    offset = (page - 1) * limit - 1
    row = db.execute(
        select(model)
        .where(model.org_id == DEMO_ORG, model.deleted_at.is_(None))
        .order_by(sort_column.desc(), model.id.desc())
        .offset(offset)
        .limit(1)
    ).scalar_one()
    scope = scope_fingerprint(resource, DEMO_ORG, {})
    return encode_cursor(getattr(row, sort_column.key), row.id, scope, settings.cursor_signing_key)


ENDPOINTS = [
    ("/api/v1/members", Member, Member.created_at, "members"),
    ("/api/v1/interactions", Interaction, Interaction.occurred_at, "interactions"),
    ("/api/v1/audit-logs", AuditLog, AuditLog.created_at, "audit-logs"),
]


@pytest.mark.parametrize("path,model,column,resource", ENDPOINTS)
def test_page_200_cursor_matches_offset(client, headers, path, model, column, resource):
    with session_scope() as db:
        cursor = _cursor_from_previous_page(db, model, column, resource)
    offset_response = client.get(
        path,
        params={"pagination": "offset", "offset": PROJECTED_OFFSET, "limit": LIMIT},
        headers=headers,
    )
    cursor_response = client.get(
        path,
        params={"pagination": "cursor", "cursor": cursor, "limit": LIMIT},
        headers=headers,
    )
    assert offset_response.status_code == cursor_response.status_code == 200
    offset_body = offset_response.json()
    cursor_body = cursor_response.json()
    assert cursor_body["pagination"] == "cursor"
    assert cursor_body["next_cursor"] is not None
    assert [item["id"] for item in cursor_body["items"]] == [
        item["id"] for item in offset_body["items"]
    ]
    assert cursor_body["items"][0]["id"] == offset_body["items"][0]["id"]
    assert len(cursor_body["items"]) == LIMIT


def test_members_cursor_walk_has_no_duplicates_and_descending_order(client, headers):
    ids: list[str] = []
    seen_cursors = set()
    cursor = None
    pages = 0
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/members", params=params, headers=headers)
        assert response.status_code == 200
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        pages += 1
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
        assert cursor and cursor not in seen_cursors
        seen_cursors.add(cursor)
        assert pages < 100
    assert len(ids) == 12_000
    assert len(set(ids)) == 12_000
    assert pages == 60

    with session_scope() as db:
        rows = db.execute(
            select(Member)
            .where(Member.org_id == DEMO_ORG, Member.deleted_at.is_(None))
            .order_by(Member.created_at.desc(), Member.id.desc())
        ).scalars()
        ordered_ids = [str(row.id) for row in rows]
    assert ids == ordered_ids


def test_cursor_is_stable_when_rows_are_inserted_at_head(client, headers):
    first = client.get("/api/v1/members", params={"limit": 10}, headers=headers).json()
    cursor = first["next_cursor"]
    assert cursor

    new_ids = []
    with session_scope() as db:
        for i in range(2):
            ts = BASE_TIME + timedelta(seconds=200_000 + i)
            row = Member(
                id=uuid.uuid4(),
                org_id=DEMO_ORG,
                site_id=DEMO_SITE,
                display_name=f"插入测试 {i}",
                company_name="虚构插入企业",
                industry="信息技术",
                tier="gold",
                status="active",
                created_at=ts,
                created_by=DEMO_USER,
                updated_at=ts,
                deleted_at=None,
            )
            db.add(row)
            new_ids.append(str(row.id))

    try:
        second = client.get(
            "/api/v1/members", params={"limit": 10, "cursor": cursor}, headers=headers
        ).json()
        assert second["items"]
        assert not ({item["id"] for item in second["items"]} & {item["id"] for item in first["items"]})
        assert not ({item["id"] for item in second["items"]} & set(new_ids))
    finally:
        with session_scope() as db:
            db.query(Member).filter(Member.id.in_([uuid.UUID(x) for x in new_ids])).delete(
                synchronize_session=False
            )


def test_cursor_can_change_page_size(client, headers):
    first = client.get("/api/v1/members", params={"limit": 10}, headers=headers).json()
    second = client.get(
        "/api/v1/members",
        params={"limit": 20, "cursor": first["next_cursor"]},
        headers=headers,
    ).json()
    assert len(second["items"]) == 20
    assert not ({item["id"] for item in second["items"]} & {item["id"] for item in first["items"]})


def test_invalid_cursor_returns_machine_readable_error(client, headers):
    response = client.get(
        "/api/v1/members", params={"cursor": "not-a-cursor"}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_CURSOR"


def test_cursor_from_other_filter_is_rejected(client, headers):
    first = client.get(
        "/api/v1/members", params={"limit": 10, "status": "active"}, headers=headers
    ).json()
    response = client.get(
        "/api/v1/members",
        params={"limit": 10, "status": "inactive", "cursor": first["next_cursor"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CURSOR_SCOPE_MISMATCH"


def test_cursor_from_other_tenant_is_rejected(client, headers):
    first = client.get("/api/v1/members", params={"limit": 10}, headers=headers).json()
    response = client.get(
        "/api/v1/members",
        params={"limit": 10, "cursor": first["next_cursor"]},
        headers={"X-Org-Id": str(SECOND_ORG)},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CURSOR_SCOPE_MISMATCH"


def test_offset_mode_still_returns_page_200_with_total(client, headers):
    response = client.get(
        "/api/v1/members",
        params={
            "pagination": "offset",
            "offset": PROJECTED_OFFSET,
            "limit": LIMIT,
            "with_total": 1,
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == "offset"
    assert body["offset"] == PROJECTED_OFFSET
    assert body["next_offset"] == PROJECTED_OFFSET + LIMIT
    assert body["total"] == 12_000
    assert len(body["items"]) == LIMIT


def test_mode_parameter_conflicts_are_explicit(client, headers):
    response = client.get(
        "/api/v1/members",
        params={"pagination": "cursor", "offset": 10, "limit": 10},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "OFFSET_NOT_ALLOWED"

    response = client.get(
        "/api/v1/members",
        params={"pagination": "offset", "cursor": "x.y", "limit": 10},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CURSOR_NOT_ALLOWED"


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_limit_is_bounded(client, headers, limit):
    response = client.get("/api/v1/members", params={"limit": limit}, headers=headers)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path,filters",
    [
        ("/api/v1/members", {"status": "active", "tier": "gold"}),
        ("/api/v1/interactions", {"channel": "wechat"}),
        ("/api/v1/audit-logs", {"action": "member.create"}),
    ],
)
def test_filters_are_applied(client, headers, path, filters):
    response = client.get(path, params={"limit": 50, **filters}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    for item in body["items"]:
        for key, value in filters.items():
            assert item[key] == value


def test_soft_deleted_rows_are_not_listed(client, headers):
    inserted = uuid.uuid4()
    with session_scope() as db:
        ts = BASE_TIME + timedelta(seconds=300_000)
        db.add(
            Member(
                id=inserted,
                org_id=DEMO_ORG,
                site_id=DEMO_SITE,
                display_name="已删除样例会员",
                company_name="虚构删除企业",
                industry="信息技术",
                tier="gold",
                status="active",
                created_at=ts,
                created_by=DEMO_USER,
                updated_at=ts,
                deleted_at=ts,
            )
        )
    try:
        body = client.get("/api/v1/members", params={"limit": 200}, headers=headers).json()
        assert str(inserted) not in {item["id"] for item in body["items"]}
    finally:
        with session_scope() as db:
            db.query(Member).filter(Member.id == inserted).delete(synchronize_session=False)


def test_cursor_after_last_row_returns_empty_page(client, headers):
    with session_scope() as db:
        offset = 11_999
        oldest = db.execute(
            select(Member)
            .where(Member.org_id == DEMO_ORG, Member.deleted_at.is_(None))
            .order_by(Member.created_at.desc(), Member.id.desc())
            .offset(offset)
            .limit(1)
        ).scalar_one()
        scope = scope_fingerprint("members", DEMO_ORG, {})
        cursor = encode_cursor(
            oldest.created_at, oldest.id, scope, settings.cursor_signing_key
        )
    body = client.get(
        "/api/v1/members", params={"limit": 50, "cursor": cursor}, headers=headers
    ).json()
    assert body["items"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    "path,resource,model,column",
    [
        ("/api/v1/interactions", "interactions", Interaction, Interaction.occurred_at),
        ("/api/v1/audit-logs", "audit-logs", AuditLog, AuditLog.created_at),
    ],
)
def test_other_lists_page_through_without_duplicates(client, headers, path, resource, model, column):
    seen = set()
    cursor = None
    for _ in range(5):
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        body = client.get(path, params=params, headers=headers).json()
        ids = {item["id"] for item in body["items"]}
        assert not (ids & seen)
        seen |= ids
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
    assert len(seen) == 250
