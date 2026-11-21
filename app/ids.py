"""UUIDv7 generation.

UUIDv7 embeds a millisecond timestamp in the high bits, so ids generated over
time are lexicographically sortable. That is useful for keyset pagination and
keeps the primary key index compact.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7(timestamp_ns: int | None = None) -> uuid.UUID:
    """Return a UUIDv7 for the given Unix nanosecond timestamp."""
    if timestamp_ns is None:
        timestamp_ns = time.time_ns()
    ms = timestamp_ns // 1_000_000
    # 48-bit timestamp, 4-bit version (0111), 12-bit rand_a, 2-bit variant
    # (10), 62-bit rand_b. Layout follows RFC 9562.
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    high = ((ms & 0xFFFFFFFFFFFF) << 16) | (0x7 << 12) | rand_a
    low = (0b10 << 62) | rand_b
    return uuid.UUID(int=(high << 64) | low)


def uuid7_for_datetime(dt) -> uuid.UUID:
    """Build a UUIDv7 whose embedded time equals ``dt`` (UTC aware)."""
    return uuid7(timestamp_ns=int(dt.timestamp() * 1_000_000_000))
