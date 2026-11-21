"""Selective JSON compression middleware (pure ASGI).

Rules:

* only ``application/json`` (and ``+json``) responses;
* only bodies >= ``minimum_size`` bytes (default 1024);
* negotiate ``br`` first, then ``gzip``; ``identity`` is always allowed;
* never double-compress, never touch SSE / already-encoded / no-transform
  responses, and never compress 1xx/204/304.

A pure ASGI middleware is used instead of ``BaseHTTPMiddleware`` because the
latter hands back a streaming response whose ``.body`` is not populated, which
would silently skip compression. Only JSON responses that advertise a
``Content-Length`` within ``max_buffer_bytes`` are buffered; anything else is
passed through untouched.
"""

from __future__ import annotations

import gzip

try:  # optional dependency; gzip still works when brotli is absent
    import brotli

    HAS_BROTLI = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    brotli = None
    HAS_BROTLI = False


def parse_accept_encoding(value: str | None) -> dict[str, float]:
    """Parse ``Accept-Encoding`` into ``{coding: q}`` (unknown codings kept)."""
    result: dict[str, float] = {}
    if not value:
        return result
    for part in value.split(","):
        piece = part.strip()
        if not piece:
            continue
        tokens = piece.split(";")
        coding = tokens[0].strip().lower()
        q = 1.0
        for param in tokens[1:]:
            param = param.strip()
            if param.lower().startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if coding == "x-gzip":
            coding = "gzip"
        result[coding] = max(result.get(coding, 0.0), q)
    return result


def choose_encoding(value: str | None, *, enable_brotli: bool = True) -> str | None:
    q_map = parse_accept_encoding(value)
    if not q_map:
        return None

    def q(coding: str) -> float:
        if coding in q_map:
            return q_map[coding]
        return q_map.get("*", 0.0)

    br_q = q("br") if enable_brotli and HAS_BROTLI else 0.0
    gzip_q = q("gzip")
    if br_q > 0 and br_q >= gzip_q:
        return "br"
    if gzip_q > 0:
        return "gzip"
    return None


def _header_value(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    lowered = name.lower().encode("ascii")
    for key, value in headers:
        if key.lower() == lowered:
            return value.decode("latin-1")
    return None


def _set_header(headers: list[tuple[bytes, bytes]], name: str, value: str) -> None:
    lowered = name.lower().encode("ascii")
    for index, (key, _) in enumerate(headers):
        if key.lower() == lowered:
            headers[index] = (key, value.encode("latin-1"))
            return
    headers.append((name.encode("ascii"), value.encode("latin-1")))


def _drop_header(headers: list[tuple[bytes, bytes]], name: str) -> None:
    lowered = name.lower().encode("ascii")
    headers[:] = [(key, value) for key, value in headers if key.lower() != lowered]


def _is_json(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _add_vary(headers: list[tuple[bytes, bytes]]) -> None:
    existing = _header_value(headers, "vary")
    if existing:
        if "accept-encoding" not in existing.lower():
            _set_header(headers, "vary", existing + ", Accept-Encoding")
    else:
        _set_header(headers, "vary", "Accept-Encoding")


class JsonCompressionMiddleware:
    def __init__(
        self,
        app,
        *,
        minimum_size: int = 1024,
        enable_brotli: bool = True,
        max_buffer_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.app = app
        self.minimum_size = max(0, int(minimum_size))
        self.enable_brotli = enable_brotli and HAS_BROTLI
        self.max_buffer_bytes = max_buffer_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_method = scope.get("method", "GET")
        request_headers = scope.get("headers", [])
        accept_encoding = _header_value(request_headers, "accept-encoding")

        start_message: dict | None = None
        buffer_mode = False
        buffered: list[bytes] = []

        async def send_wrapper(message):
            nonlocal start_message, buffer_mode
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                content_type = _header_value(headers, "content-type") or ""
                if not _is_json(content_type):
                    await send(message)
                    return
                _add_vary(headers)
                message = {**message, "headers": headers}

                status = message.get("status", 200)
                already_encoded = _header_value(headers, "content-encoding")
                no_transform = "no-transform" in (
                    _header_value(headers, "cache-control") or ""
                ).lower()
                transfer_encoding = _header_value(headers, "transfer-encoding")
                raw_length = _header_value(headers, "content-length")
                length = int(raw_length) if raw_length and raw_length.isdigit() else None

                compressible = (
                    request_method != "HEAD"
                    and 200 <= status < 300
                    and status not in (204, 304)
                    and not already_encoded
                    and not no_transform
                    and not transfer_encoding
                    and length is not None
                    and self.minimum_size <= length <= self.max_buffer_bytes
                    and choose_encoding(accept_encoding, enable_brotli=self.enable_brotli)
                    is not None
                )
                if compressible:
                    start_message = message
                    buffer_mode = True
                else:
                    await send(message)
                return

            if message["type"] == "http.response.body" and buffer_mode:
                buffered.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(buffered)
                encoding = choose_encoding(
                    accept_encoding, enable_brotli=self.enable_brotli
                )
                if body and encoding and len(body) >= self.minimum_size:
                    if encoding == "br":
                        compressed = brotli.compress(body, quality=5)
                    else:
                        compressed = gzip.compress(body, compresslevel=6, mtime=0)
                    headers = list(start_message.get("headers", []))
                    _set_header(headers, "content-encoding", encoding)
                    _set_header(headers, "content-length", str(len(compressed)))
                    _set_header(headers, "x-uncompressed-length", str(len(body)))
                    await send({**start_message, "headers": headers})
                    await send(
                        {
                            "type": "http.response.body",
                            "body": compressed,
                            "more_body": False,
                        }
                    )
                else:
                    # Body turned out smaller than advertised/expected: send
                    # the original bytes but keep the Vary header.
                    headers = list(start_message.get("headers", []))
                    _set_header(headers, "content-length", str(len(body)))
                    await send({**start_message, "headers": headers})
                    await send(
                        {
                            "type": "http.response.body",
                            "body": body,
                            "more_body": False,
                        }
                    )
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
