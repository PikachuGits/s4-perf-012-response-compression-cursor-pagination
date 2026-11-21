from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.cursor import (
    CursorError,
    decode_cursor,
    encode_cursor,
    normalize_ts,
    scope_fingerprint,
)
from app.ids import uuid7


def test_scope_fingerprint_is_order_independent_and_ignores_empty_values():
    first = scope_fingerprint("members", uuid.UUID(int=1), {"b": "2", "a": "1", "c": None})
    second = scope_fingerprint("members", uuid.UUID(int=1), {"a": "1", "b": "2"})
    assert first == second
    assert first != scope_fingerprint("members", uuid.UUID(int=2), {"a": "1", "b": "2"})


def test_encode_decode_roundtrip_preserves_microseconds_and_uuid():
    ts = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=timezone.utc)
    row_id = uuid7()
    scope = scope_fingerprint("members", uuid.UUID(int=3), {})
    cursor = encode_cursor(ts, row_id, scope, "unit-key")
    key = decode_cursor(cursor, scope, "unit-key")
    assert key.ts == ts
    assert key.id == row_id


def test_tampered_cursor_is_rejected():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope = scope_fingerprint("members", uuid.UUID(int=3), {})
    cursor = encode_cursor(ts, uuid7(), scope, "unit-key")
    payload, signature = cursor.split(".")
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + signature
    with pytest.raises(CursorError) as exc:
        decode_cursor(tampered, scope, "unit-key")
    assert exc.value.code == "INVALID_CURSOR"


def test_cursor_scope_mismatch_is_explicit():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope_a = scope_fingerprint("members", uuid.UUID(int=3), {})
    scope_b = scope_fingerprint("members", uuid.UUID(int=3), {"status": "active"})
    cursor = encode_cursor(ts, uuid7(), scope_a, "unit-key")
    with pytest.raises(CursorError) as exc:
        decode_cursor(cursor, scope_b, "unit-key")
    assert exc.value.code == "CURSOR_SCOPE_MISMATCH"


def test_wrong_signing_key_is_rejected():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope = scope_fingerprint("members", uuid.UUID(int=3), {})
    cursor = encode_cursor(ts, uuid7(), scope, "key-a")
    with pytest.raises(CursorError):
        decode_cursor(cursor, scope, "key-b")


def test_naive_timestamp_is_treated_as_utc():
    naive = datetime(2026, 1, 1, 12, 0, 0, 42)
    assert normalize_ts(naive).tzinfo == timezone.utc
    assert normalize_ts(naive).hour == 12


def test_uuid7_is_lexicographically_time_ordered():
    first = uuid7(timestamp_ns=1_700_000_000_000_000_000)
    later = uuid7(timestamp_ns=1_700_000_001_000_000_000)
    assert str(first) < str(later)
    assert first.version == 7
    assert later.version == 7
