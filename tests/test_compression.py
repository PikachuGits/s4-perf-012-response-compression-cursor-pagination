from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.compression import (
    HAS_BROTLI,
    JsonCompressionMiddleware,
    choose_encoding,
    parse_accept_encoding,
)


def test_large_json_is_gzipped_and_decodes_to_full_payload(client, headers):
    response = client.get(
        "/api/v1/members?limit=100",
        headers={**headers, "Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    original = int(response.headers["x-uncompressed-length"])
    compressed = int(response.headers["content-length"])
    assert original > 1024
    assert compressed < original
    assert len(response.json()["items"]) == 100


@pytest.mark.skipif(not HAS_BROTLI, reason="brotli not installed")
def test_brotli_is_preferred_when_client_accepts_both(client, headers):
    response = client.get(
        "/api/v1/interactions?limit=100",
        headers={**headers, "Accept-Encoding": "gzip, br"},
    )
    assert response.headers["content-encoding"] == "br"
    assert len(response.json()["items"]) == 100


def test_identity_gets_uncompressed_json(client, headers):
    response = client.get(
        "/api/v1/audit-logs?limit=100",
        headers={**headers, "Accept-Encoding": "identity"},
    )
    assert "content-encoding" not in response.headers
    assert len(response.json()["items"]) == 100


def test_small_json_is_not_compressed(client, headers):
    response = client.get(
        "/api/v1/meta/dictionaries",
        headers={**headers, "Accept-Encoding": "gzip, br"},
    )
    assert response.status_code == 200
    assert len(response.content) < 1024
    assert "content-encoding" not in response.headers


def test_q_zero_disables_compression(client, headers):
    response = client.get(
        "/api/v1/members?limit=100",
        headers={**headers, "Accept-Encoding": "gzip;q=0, br;q=0, identity"},
    )
    assert "content-encoding" not in response.headers


def test_parse_and_choose_accept_encoding():
    parsed = parse_accept_encoding("gzip;q=0.8, br;q=1.0, *;q=0.1")
    assert parsed["gzip"] == 0.8
    assert parsed["br"] == 1.0
    assert choose_encoding("gzip;q=0.8, br;q=1.0") == "br"
    assert choose_encoding("gzip;q=0.9, br;q=0.8") == "gzip"
    assert choose_encoding("identity") is None
    assert choose_encoding("*") in ("gzip", "br")
    assert choose_encoding("gzip;q=0, br;q=0") is None


def _exact_json_app(minimum_size: int) -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(
        JsonCompressionMiddleware, minimum_size=minimum_size, enable_brotli=False
    )

    @mini.get("/fixed/{size}")
    def fixed(size: int):
        prefix = b'{"data":"'
        suffix = b'"}'
        fill = size - len(prefix) - len(suffix)
        assert fill >= 0
        return Response(
            content=prefix + (b"x" * fill) + suffix,
            media_type="application/json",
        )

    @mini.get("/no-transform")
    def no_transform():
        return Response(
            content=json.dumps({"data": "x" * 2000}),
            media_type="application/json",
            headers={"Cache-Control": "no-transform"},
        )

    @mini.get("/text")
    def text():
        return Response(content="x" * 5000, media_type="text/plain")

    return mini


def test_threshold_boundary_is_exact():
    with TestClient(_exact_json_app(1024)) as mini:
        below = mini.get("/fixed/1023", headers={"Accept-Encoding": "gzip"})
        at = mini.get("/fixed/1024", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in below.headers
    assert at.headers["content-encoding"] == "gzip"
    assert int(at.headers["x-uncompressed-length"]) == 1024


def test_never_compresses_no_transform_or_non_json():
    with TestClient(_exact_json_app(1024)) as mini:
        no_transform = mini.get("/no-transform", headers={"Accept-Encoding": "gzip"})
        text = mini.get("/text", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in no_transform.headers
    assert "content-encoding" not in text.headers
