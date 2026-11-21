#!/usr/bin/env python
"""Measure compression and page-200 latency against a real HTTP server.

The script talks raw HTTP (``http.client``) so compressed bytes are not
transparently decoded by the client. It starts uvicorn by default and stops it
on exit; pass ``--base-url`` to reuse a server you started yourself.
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from app.constants import DEMO_ORG  # noqa: E402
from app.cursor import encode_cursor, scope_fingerprint  # noqa: E402
from app.db import build_engine, sessionmaker  # noqa: E402
from app.models import AuditLog, Interaction, Member  # noqa: E402

try:
    import brotli
except ImportError:  # pragma: no cover
    brotli = None


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = max(0, min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[k]


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "mean_ms": round(statistics.fmean(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }


class Client:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.conn = http.client.HTTPConnection(host, port, timeout=20)

    def reset(self) -> None:
        self.conn.close()
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=20)

    def get(self, path: str, encoding: str | None) -> tuple[bytes, dict, float]:
        headers = {"Accept-Encoding": encoding or "identity", "Connection": "keep-alive"}
        start = time.perf_counter()
        try:
            self.conn.request("GET", path, headers=headers)
            response = self.conn.getresponse()
            raw = response.read()
        except (http.client.HTTPException, ConnectionError, OSError):
            self.reset()
            self.conn.request("GET", path, headers=headers)
            response = self.conn.getresponse()
            raw = response.read()
        elapsed_ms = (time.perf_counter() - start) * 1000
        meta = {
            "status": response.status,
            "content_encoding": response.getheader("Content-Encoding") or "identity",
            "content_length": response.getheader("Content-Length"),
            "uncompressed_length": response.getheader("X-Uncompressed-Length"),
            "vary": response.getheader("Vary"),
        }
        return raw, meta, elapsed_ms


def decode_body(raw: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(raw)
    if encoding == "br":
        if brotli is None:
            raise RuntimeError("brotli module not installed")
        return brotli.decompress(raw)
    return raw


def connect(base_url: str | None, port: int):
    if base_url:
        parsed = urllib.parse.urlsplit(base_url)
        return parsed.hostname or "127.0.0.1", parsed.port or 80
    return "127.0.0.1", port


def wait_health(client: Client, proc: subprocess.Popen | None, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            _, meta, _ = client.get("/health", None)
            if meta["status"] == 200:
                return
        except Exception as exc:  # noqa: BLE001 - startup probe
            last_error = exc
            client.reset()
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early: {proc.returncode}")
        time.sleep(0.25)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def collect_compression(client: Client, repeats: int, small_path: str) -> list[dict]:
    targets = [
        ("members", "/api/v1/members?limit=100"),
        ("interactions", "/api/v1/interactions?limit=100"),
        ("audit_logs", "/api/v1/audit-logs?limit=100"),
    ]
    rows: list[dict] = []
    for name, path in targets:
        for encoding in ("identity", "gzip", "br"):
            client.get(path, encoding)

        measured: dict[str, list[tuple[int, float]]] = {"identity": [], "gzip": [], "br": []}
        for encoding in ("identity", "gzip", "br"):
            if encoding == "br" and brotli is None:
                continue
            for _ in range(repeats):
                raw, meta, elapsed = client.get(path, encoding)
                if encoding != "identity" and meta["content_encoding"] != encoding:
                    raise RuntimeError(
                        f"{path}: requested {encoding}, got {meta['content_encoding']}"
                    )
                body = decode_body(raw, meta["content_encoding"])
                parsed = json.loads(body)
                if len(parsed["items"]) != 100:
                    raise RuntimeError(f"{path}: expected 100 items, got {len(parsed['items'])}")
                measured[encoding].append((len(raw), elapsed))

        identity_bytes = statistics.median([b for b, _ in measured["identity"]])
        row = {
            "endpoint": name,
            "path": path,
            "items": 100,
            "identity": {
                "bytes": int(identity_bytes),
                "latency": summarize([t for _, t in measured["identity"]]),
            },
            "encodings": {},
        }
        for encoding in ("gzip", "br"):
            if not measured[encoding]:
                continue
            size = statistics.median([b for b, _ in measured[encoding]])
            row["encodings"][encoding] = {
                "bytes": int(size),
                "ratio": round(1 - size / identity_bytes, 4),
                "saved_bytes": int(identity_bytes - size),
                "latency": summarize([t for _, t in measured[encoding]]),
            }
        rows.append(row)

    raw, meta, _ = client.get(small_path, "gzip")
    rows.append(
        {
            "endpoint": "small_json_control",
            "path": small_path,
            "identity": {"bytes": len(raw)},
            "encodings": {},
            "note": "低于 1 KB 阈值的 JSON 不压缩（control）",
            "content_encoding": meta["content_encoding"],
        }
    )
    return rows


def cursor_for_page(db, model, sort_column, resource: str, page: int, limit: int, org_id) -> str:
    offset = (page - 1) * limit - 1
    stmt = (
        select(model)
        .where(model.org_id == org_id, model.deleted_at.is_(None))
        .order_by(sort_column.desc(), model.id.desc())
        .offset(offset)
        .limit(1)
    )
    row = db.execute(stmt).scalar_one()
    scope = scope_fingerprint(resource, org_id, {})
    key = os.getenv("CURSOR_SIGNING_KEY", "local-dev-only-not-a-production-secret")
    return encode_cursor(getattr(row, sort_column.key), row.id, scope, key)


def _plan_exec_ms(lines: list[str]) -> float | None:
    for line in lines:
        match = re.search(r"Execution Time: ([0-9.]+) ms", line)
        if match:
            return float(match.group(1))
    return None


def _time_sql(db, sql: str, params: dict, repeats: int = 15) -> dict:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        db.execute(text(sql), params).fetchall()
        times.append((time.perf_counter() - start) * 1000)
    return summarize(times)


def explain_pagination(db, model, sort_column, resource: str, page: int, limit: int, org_id):
    """Return EXPLAIN output and DB timings for offset vs keyset, same page."""
    engine = db.get_bind()
    offset = (page - 1) * limit
    sort_name = sort_column.name
    table = model.__tablename__
    offset_sql_base = (
        f"SELECT id FROM {table} WHERE org_id = :org AND deleted_at IS NULL "
        f"ORDER BY {sort_name} DESC, id DESC"
    )
    offset_sql = f"{offset_sql_base} LIMIT {limit + 1} OFFSET {offset}"

    cursor_row = db.execute(
        select(model)
        .where(model.org_id == org_id, model.deleted_at.is_(None))
        .order_by(sort_column.desc(), model.id.desc())
        .offset(offset - 1)
        .limit(1)
    ).scalar_one()
    ts = getattr(cursor_row, sort_column.key)

    if engine.dialect.name == "postgresql":
        cursor_sql = (
            f"SELECT id FROM {table} WHERE org_id = :org AND deleted_at IS NULL "
            f"AND ({sort_name}, id) < (:ts, CAST(:rid AS uuid)) "
            f"ORDER BY {sort_name} DESC, id DESC LIMIT {limit + 1}"
        )
        offset_plan = db.execute(
            text("EXPLAIN (ANALYZE, BUFFERS) " + offset_sql), {"org": str(org_id)}
        ).fetchall()
        cursor_plan = db.execute(
            text("EXPLAIN (ANALYZE, BUFFERS) " + cursor_sql),
            {"org": str(org_id), "ts": ts, "rid": str(cursor_row.id)},
        ).fetchall()
        return {
            "dialect": "postgresql",
            "offset": [row[0] for row in offset_plan],
            "cursor": [row[0] for row in cursor_plan],
            "execution_ms": {
                "offset": _plan_exec_ms([row[0] for row in offset_plan]),
                "cursor": _plan_exec_ms([row[0] for row in cursor_plan]),
            },
            "timing_ms": {
                "offset": _time_sql(db, offset_sql, {"org": str(org_id)}),
                "cursor": _time_sql(
                    db,
                    cursor_sql,
                    {"org": str(org_id), "ts": ts, "rid": str(cursor_row.id)},
                ),
            },
        }

    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    cursor_sql = (
        f"SELECT id FROM {table} WHERE org_id = :org AND deleted_at IS NULL "
        f"AND ({sort_name}, id) < (:ts, :rid) "
        f"ORDER BY {sort_name} DESC, id DESC LIMIT {limit + 1}"
    )
    offset_plan = db.execute(
        text("EXPLAIN QUERY PLAN " + offset_sql), {"org": str(org_id)}
    ).fetchall()
    cursor_plan = db.execute(
        text("EXPLAIN QUERY PLAN " + cursor_sql),
        {"org": str(org_id), "ts": ts, "rid": cursor_row.id.hex},
    ).fetchall()
    return {
        "dialect": "sqlite",
        "offset": [row[-1] for row in offset_plan],
        "cursor": [row[-1] for row in cursor_plan],
        "execution_ms": {"offset": None, "cursor": None},
        "timing_ms": {
            "offset": _time_sql(db, offset_sql, {"org": str(org_id)}),
            "cursor": _time_sql(
                db,
                cursor_sql,
                {"org": str(org_id), "ts": ts, "rid": cursor_row.id.hex},
            ),
        },
    }


def collect_pagination(client: Client, db, page: int, limit: int, repeats: int) -> list[dict]:
    targets = [
        ("members", Member, Member.created_at, "members"),
        ("interactions", Interaction, Interaction.occurred_at, "interactions"),
        ("audit_logs", AuditLog, AuditLog.created_at, "audit-logs"),
    ]
    rows: list[dict] = []
    for name, model, sort_column, resource in targets:
        cursor = cursor_for_page(db, model, sort_column, resource, page, limit, DEMO_ORG)
        offset = (page - 1) * limit
        path_name = name.replace("_", "-")
        offset_path = f"/api/v1/{path_name}?pagination=offset&offset={offset}&limit={limit}"
        cursor_path = (
            f"/api/v1/{path_name}?pagination=cursor&limit={limit}"
            f"&cursor={urllib.parse.quote(cursor)}"
        )
        total_path = offset_path + "&with_total=1"

        for path in (offset_path, cursor_path):
            client.get(path, "identity")
        raw_offset, _, _ = client.get(offset_path, "identity")
        raw_cursor, _, _ = client.get(cursor_path, "identity")
        offset_ids = [item["id"] for item in json.loads(raw_offset)["items"]]
        cursor_ids = [item["id"] for item in json.loads(raw_cursor)["items"]]
        if offset_ids != cursor_ids:
            raise RuntimeError(f"{name}: cursor and offset page {page} differ")

        offset_times: list[float] = []
        cursor_times: list[float] = []
        for _ in range(repeats):
            _, _, t_offset = client.get(offset_path, "identity")
            _, _, t_cursor = client.get(cursor_path, "identity")
            offset_times.append(t_offset)
            cursor_times.append(t_cursor)

        total_times: list[float] = []
        for _ in range(max(5, repeats // 3)):
            _, _, t_total = client.get(total_path, "identity")
            total_times.append(t_total)

        rows.append(
            {
                "endpoint": name,
                "page": page,
                "limit": limit,
                "offset": offset,
                "offset_mode": {
                    "path": offset_path,
                    "latency": summarize(offset_times),
                    "bytes": len(raw_offset),
                },
                "cursor_mode": {
                    "path": cursor_path,
                    "latency": summarize(cursor_times),
                    "bytes": len(raw_cursor),
                    "cursor_chars": len(cursor),
                },
                "offset_with_total": {"latency": summarize(total_times)},
                "speedup_median": round(
                    statistics.median(offset_times) / statistics.median(cursor_times), 2
                ),
                "explain": explain_pagination(db, model, sort_column, resource, page, limit, DEMO_ORG),
                "first_item_equal": offset_ids[0] == cursor_ids[0],
                "items": len(offset_ids),
            }
        )
    return rows


def _plan_short(plan: list[str], max_len: int = 180) -> str:
    if not plan:
        return "—"
    interesting = [
        line.strip()
        for line in plan
        if any(token in line for token in ("Index Scan", "Bitmap Heap Scan", "Seq Scan", "SEARCH"))
    ]
    text = interesting[0] if interesting else plan[0].strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text.replace("|", "\\|")


def render_markdown(report: dict) -> str:
    env = report["environment"]
    lines = [
        "# S4-PERF-012 实测结果",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 数据库：{env['database_dialect']} {env['database_version']}",
        f"- Python：{env['python']}；重复次数：{env['repeats']}",
        "",
        "## 1. JSON 压缩（100 条/页）",
        "",
        "| 接口 | 原始体积 | gzip | 节省 | br | 节省 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["compression"]:
        if row["endpoint"] == "small_json_control":
            continue
        gzip_info = row["encodings"].get("gzip", {})
        br_info = row["encodings"].get("br", {})
        lines.append(
            f"| {row['endpoint']} | {row['identity']['bytes']:,} B | "
            f"{gzip_info.get('bytes', 0):,} B | {gzip_info.get('ratio', 0) * 100:.1f}% | "
            f"{br_info.get('bytes', 0):,} B | {br_info.get('ratio', 0) * 100:.1f}% |"
        )
    small = next((r for r in report["compression"] if r["endpoint"] == "small_json_control"), None)
    if small:
        lines += [
            "",
            f"低于 1 KB 的对照接口（{small['path']}，{small['identity']['bytes']} B）"
            f"的 `Content-Encoding` 为 `{small.get('content_encoding')}`，未被压缩。",
        ]

    first = report["pagination"][0]
    lines += [
        "",
        f"## 2. 第 {first['page']} 页响应时间（limit={first['limit']}）",
        "",
        "| 接口 | offset HTTP 中位数 | 游标 HTTP 中位数 | 提速 | offset P95 | 游标 P95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["pagination"]:
        o = row["offset_mode"]["latency"]
        c = row["cursor_mode"]["latency"]
        lines.append(
            f"| {row['endpoint']} | {o['median_ms']:.2f} ms | {c['median_ms']:.2f} ms | "
            f"{row['speedup_median']:.2f}× | {o['p95_ms']:.2f} ms | {c['p95_ms']:.2f} ms |"
        )
    lines += [
        "",
        "> HTTP 列是端到端响应时间（含连接复用、FastAPI 序列化与事件循环开销），"
        "固定地板约 1 ms。offset 模式的 `total` 未计入（`with_total` 单独测量），"
        "以保证对比只反映分页方式本身。",
        "",
        "### 2.1 数据库层对比（排除 HTTP 地板）",
        "",
    ]

    if env["database_dialect"] == "postgresql":
        lines += [
            "| 接口 | offset EXPLAIN | 游标 EXPLAIN | EXPLAIN 提速 | offset 查询往返 | 游标查询往返 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in report["pagination"]:
            ex = row["explain"].get("execution_ms", {})
            db_t = row["explain"].get("timing_ms", {})
            if ex.get("offset") and ex.get("cursor"):
                plan_speed = f"{ex['offset'] / ex['cursor']:.0f}×"
                plan_o = f"{ex['offset']:.3f} ms"
                plan_c = f"{ex['cursor']:.3f} ms"
            else:
                plan_speed, plan_o, plan_c = "—", "—", "—"
            db_o = db_t.get("offset", {}).get("median_ms", 0.0)
            db_c = db_t.get("cursor", {}).get("median_ms", 0.0)
            lines.append(
                f"| {row['endpoint']} | {plan_o} | {plan_c} | {plan_speed} | "
                f"{db_o:.2f} ms | {db_c:.2f} ms |"
            )
        lines += [
            "",
            "> PostgreSQL 的 EXPLAIN 值是 `EXPLAIN (ANALYZE, BUFFERS)` 的 Execution Time；"
            "查询往返值是同一 SQL 经 SQLAlchemy 执行的中位数（含驱动与编译开销）。",
        ]
    else:
        lines += [
            "SQLite 的 `EXPLAIN QUERY PLAN` 不提供执行耗时，因此用查询计划直接对比："
            "游标查询的索引条件包含 `(排序键, id) < (?, ?)`，offset 查询只有 `org_id=?`。"
            "查询计划见 2.2。",
        ]

    lines += [
        "",
        "### 2.2 查询计划关键行",
        "",
        "| 接口 | offset | 游标 |",
        "|---|---|---|",
    ]
    for row in report["pagination"]:
        lines.append(
            f"| {row['endpoint']} | `{_plan_short(row['explain']['offset'])}` | "
            f"`{_plan_short(row['explain']['cursor'])}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="复用已启动的服务，例如 http://127.0.0.1:8021")
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--page", type=int, default=200)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output", default="evidence/benchmark.json")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL", "sqlite+pysqlite:///./.data/bench.sqlite3")
    engine = build_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        member_count = db.execute(select(func.count()).select_from(Member)).scalar_one()
        if member_count == 0:
            raise SystemExit("数据库为空，请先运行 scripts/seed_benchmark.py")
        if member_count < args.page * args.limit:
            raise SystemExit(
                f"数据量不足：members={member_count}，第 {args.page} 页需要 "
                f">= {args.page * args.limit} 行"
            )

    host, port = connect(args.base_url, args.port)
    proc = None
    client = Client(host, port)
    if not args.base_url:
        env = os.environ.copy()
        env["AUTO_CREATE_SCHEMA"] = "0"
        env["SEED_DEMO_DATA"] = "0"
        env["ENABLE_OFFSET_BASELINE"] = "1"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    started = time.perf_counter()
    try:
        wait_health(client, proc)
        startup_ms = round((time.perf_counter() - started) * 1000, 1)
        with Session() as db:
            compression = collect_compression(
                client, max(5, args.repeats // 2), "/api/v1/meta/dictionaries"
            )
            pagination = collect_pagination(client, db, args.page, args.limit, args.repeats)
            dialect = db.get_bind().dialect.name
            if dialect == "postgresql":
                version = db.execute(text("SHOW server_version")).scalar_one()
            else:
                version = db.execute(text("SELECT sqlite_version()")).scalar_one()

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "database_dialect": dialect,
                "database_version": version,
                "server_startup_ms": startup_ms,
                "repeats": args.repeats,
                "compression_min_bytes": int(os.getenv("COMPRESSION_MIN_BYTES", "1024")),
                "brotli_available": brotli is not None,
            },
            "compression": compression,
            "pagination": pagination,
        }
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown = render_markdown(report)
        output.with_suffix(".md").write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"\nwrote {output} and {output.with_suffix('.md')}")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.returncode not in (0, -15):
                print("---- server log ----", file=sys.stderr)
                assert proc.stdout is not None
                print(proc.stdout.read(), file=sys.stderr)


if __name__ == "__main__":
    main()
