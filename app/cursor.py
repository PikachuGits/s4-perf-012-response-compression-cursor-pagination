"""Opaque, signed keyset-pagination cursors.

A cursor is ``base64url(payload) + "." + base64url(hmac_sha256(payload))``.
The payload binds the sort key (timestamp + UUID), the resource name and the
filter/tenant scope. Reusing a cursor with another tenant or another filter
therefore fails with ``CURSOR_SCOPE_MISMATCH`` instead of silently leaking or
skipping rows.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

CURSOR_VERSION = 1


class CursorError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class CursorKey:
    ts: datetime
    id: uuid.UUID


def normalize_ts(value: datetime) -> datetime:
    """Return an aware UTC datetime; SQLite returns naive values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def scope_fingerprint(resource: str, org_id: uuid.UUID, filters: dict[str, str | None]) -> str:
    canonical = resource + "|" + str(org_id) + "|" + "|".join(
        f"{key}={filters[key]}" for key in sorted(filters) if filters[key] not in (None, "")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_cursor(ts: datetime, row_id: uuid.UUID, scope: str, key: str) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "s": scope,
        "t": normalize_ts(ts).isoformat(timespec="microseconds"),
        "i": str(row_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
    return _b64encode(raw) + "." + _b64encode(signature)


def decode_cursor(cursor: str, scope: str, key: str) -> CursorKey:
    if not cursor or cursor.count(".") != 1:
        raise CursorError("INVALID_CURSOR", "游标格式不正确，请使用上一页返回的 next_cursor")
    payload_part, signature_part = cursor.split(".", 1)
    try:
        raw = _b64decode(payload_part)
        signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise CursorError("INVALID_CURSOR", "游标编码无法解析") from exc
    expected = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise CursorError("INVALID_CURSOR", "游标签名校验失败，请勿手工修改游标")
    try:
        payload = json.loads(raw)
        if payload.get("v") != CURSOR_VERSION:
            raise ValueError("unsupported version")
        if payload.get("s") != scope:
            raise CursorError(
                "CURSOR_SCOPE_MISMATCH",
                "游标与当前租户或筛选条件不匹配，请在当前筛选条件的第一页重新开始",
            )
        ts = datetime.fromisoformat(str(payload["t"]).replace("Z", "+00:00"))
        row_id = uuid.UUID(str(payload["i"]))
    except CursorError:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise CursorError("INVALID_CURSOR", "游标内容不完整或已损坏") from exc
    return CursorKey(ts=normalize_ts(ts), id=row_id)
