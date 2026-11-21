"""Runtime configuration.

All values come from environment variables (see README). No client name or
credential is hard-coded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite+pysqlite:///./.data/perf012.sqlite3",
    )
    # Local harness convenience. A deployment runs `alembic upgrade head`
    # instead and keeps AUTO_CREATE_SCHEMA=0.
    auto_create_schema: bool = os.getenv("AUTO_CREATE_SCHEMA", "1") == "1"
    seed_demo_data: bool = os.getenv("SEED_DEMO_DATA", "0") == "1"
    # Cursor signing key. In production this MUST be provided by the
    # environment; the fallback is intentionally marked as local-only.
    cursor_signing_key: str = os.getenv(
        "CURSOR_SIGNING_KEY", "local-dev-only-not-a-production-secret"
    )
    # Compression threshold in bytes. Only application/json is compressed.
    compression_min_bytes: int = int(os.getenv("COMPRESSION_MIN_BYTES", "1024"))
    compression_enable_brotli: bool = os.getenv("COMPRESSION_ENABLE_BROTLI", "1") == "1"
    # Legacy offset mode is kept only for the before/after benchmark and a
    # short rollout window. Set 0 to remove it from the public surface.
    enable_offset_baseline: bool = os.getenv("ENABLE_OFFSET_BASELINE", "1") == "1"


settings = Settings()
