#!/usr/bin/env python
"""Dump the live OpenAPI document to docs/openapi.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./.data/openapi.sqlite3")

from app.main import app  # noqa: E402

target = ROOT / "docs" / "openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"wrote {target}")
