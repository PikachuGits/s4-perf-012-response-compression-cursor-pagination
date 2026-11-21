import json
from pathlib import Path

import pytest

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
PATHS = ["/api/v1/members", "/api/v1/interactions", "/api/v1/audit-logs"]


@pytest.mark.parametrize("path", PATHS)
def test_openapi_documents_cursor_and_deprecated_offset(path):
    schema = app.openapi()
    operation = schema["paths"][path]["get"]
    params = {param["name"]: param for param in operation["parameters"]}
    assert {"cursor", "limit", "pagination", "offset", "with_total"} <= set(params)
    assert params["cursor"]["description"]
    assert params["pagination"]["schema"]["default"] == "cursor"
    assert params["offset"]["deprecated"] is True
    assert "废弃" in params["offset"]["description"]
    assert "next_cursor" in operation["description"] or "游标" in operation["summary"]


def test_page_models_expose_pagination_fields():
    schema = app.openapi()
    member_page = schema["components"]["schemas"]["MemberPage"]
    fields = member_page["properties"]
    assert {"items", "limit", "next_cursor", "has_more", "pagination", "offset", "next_offset", "total"} <= set(fields)
    assert "cursor" in fields["next_cursor"]["description"]


def test_docs_markdown_and_exported_openapi_exist():
    markdown = (ROOT / "docs" / "pagination.md").read_text(encoding="utf-8")
    for token in ("next_cursor", "pagination=cursor", "CURSOR_SCOPE_MISMATCH", "第 200 页"):
        assert token in markdown
    exported = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert exported["info"]["title"] == app.openapi()["info"]["title"]
    for path in PATHS:
        assert path in exported["paths"]
