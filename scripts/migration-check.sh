#!/usr/bin/env bash
# Round-trip the migration on an isolated PostgreSQL database.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DATABASE_URL:?请先导出隔离库 DATABASE_URL，例如 postgresql+psycopg://perf012@127.0.0.1:55433/perf012_test}"

lower_url=$(printf '%s' "$DATABASE_URL" | tr '[:upper:]' '[:lower:]')
case "$lower_url" in
  *bench*|*test*|*perf012*) ;;
  *) echo "拒绝执行：DATABASE_URL 不含 bench/test/perf012 标识" >&2; exit 2 ;;
esac

echo "== downgrade base =="
.venv/bin/alembic downgrade base
echo "== upgrade head =="
.venv/bin/alembic upgrade head
echo "== tables =="
.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, inspect, text
import os

engine = create_engine(os.environ["DATABASE_URL"])
inspector = inspect(engine)
tables = sorted(inspector.get_table_names())
print("tables:", ", ".join(tables))
for table in ("members", "interactions", "audit_logs"):
    indexes = sorted(index["name"] for index in inspector.get_indexes(table))
    print(f"indexes[{table}]:", ", ".join(indexes))
with engine.connect() as conn:
    print("server:", conn.execute(text("SHOW server_version")).scalar_one())
PY
