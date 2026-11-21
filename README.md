# S4-PERF-012 · 接口响应压缩与分页方式优化

本仓库是工单 **S4-PERF-012** 的交付物：为会员空间平台的三个深分页列表接口
（会员、互动记录、审计查询）加上 **JSON 响应压缩**，并把 `limit/offset` 改为
**签名游标（keyset）分页**；同时给出压缩前后体积、第 200 页响应时间与查询计划的前后对比。

- 公开仓库：`https://github.com/PikachuGits/s4-perf-012-response-compression-cursor-pagination`
- 交付 PR（已合并）：`https://github.com/PikachuGits/s4-perf-012-response-compression-cursor-pagination/pull/1`
- 默认分支：`main`
- 本次实测：SQLite 3.50.4 与 PostgreSQL 16.16 各 `41 passed`，覆盖率 91% / 90%
- 证据：`evidence/test-results.txt`、`evidence/test-results-postgres.txt`、
  `evidence/benchmark-pg16.md`、`evidence/benchmark-sqlite.md`、`evidence/migration-check.txt`

> **边界声明（先读）**
>
> 1. 客户真实产品代码与 S2-CRM 服务不在本仓库内。`app/` 是**可独立复现的接口桩/参考实现**，
>    用于承载本单的压缩与分页改动，不声称它是客户最终实现。
> 2. 依赖 S2-CRM（CRM 核心对象）的部分用 `Member` 桩表模拟；对接点见「对接真实服务」。
> 3. 全部数据为脚本生成的虚构数据（姓名/企业名均带“样例/虚构”前缀），不含客户名称、
>    真实个人信息或真实企业信息。
> 4. 本仓库产出的是代码、测试与证据，**不代表买方验收或结算**。

---

## 1. 验收映射

| 验收标准（原文） | 实现位置 | 可复现证据 |
|---|---|---|
| ① 主要列表接口压缩前后体积对比有数据 | `app/compression.py`；`scripts/run_benchmark.py` | `evidence/benchmark-pg16.md` 第 1 节、`evidence/benchmark-sqlite.md` 第 1 节：三个接口 identity / gzip / br 的原始字节、压缩字节、节省比例 |
| ② 3 个接口改为游标分页并有第 200 页响应时间对比 | `app/cursor.py`、`app/pagination.py`、`app/main.py` | `tests/test_pagination.py` 逐页无重复/无遗漏；`evidence/benchmark-*.md` 第 2 节：offset vs cursor 第 200 页 HTTP 中位数、P95，以及 `EXPLAIN` 数据库执行时间 |
| ③ 接口文档同步更新分页参数 | `docs/pagination.md`、`docs/openapi.json`、FastAPI 参数描述 | `tests/test_openapi.py` 校验 `cursor/limit/pagination/offset/with_total`、`offset` deprecated 标记与导出文档存在 |
| 范围补充：>1 KB JSON 才压缩 | `app/compression.py` 的 `minimum_size=1024` | `tests/test_compression.py::test_threshold_boundary_is_exact`：1023 B 不压缩、1024 B 压缩；`/api/v1/meta/dictionaries`（195 B）保持 identity |
| 范围补充：br 或 gzip | `app/compression.py` 按 q 值协商 | `tests/test_compression.py`：`gzip`、`br`、`gzip;q=0`、`Accept-Encoding: *` 全覆盖；未安装 brotli 时自动退回 gzip |

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `app/compression.py` | 新增纯 ASGI 的 `JsonCompressionMiddleware`：仅压缩 JSON、阈值 1024 B、br 优先 gzip、`Vary: Accept-Encoding`、跳过 SSE/已编码/`no-transform`/204/304；压缩后写 `Content-Length` 与 `X-Uncompressed-Length` |
| `app/cursor.py` | 新增不透明游标：`base64url(payload).base64url(HMAC-SHA256)`；绑定 `resource + org_id + 筛选条件`，篡改/跨租户/跨筛选返回明确错误码 |
| `app/pagination.py` | 新增 `paginate_cursor` 与 `paginate_offset`；游标查询用**行比较** `(排序键, id) < (:ts, :id)`，让 PostgreSQL/SQLite 把条件下推进复合索引 |
| `app/models.py` | 三张业务表；索引 `(org_id, created_at, id)`、`(org_id, occurred_at, id)`、`(org_id, action/actor, created_at, id)` |
| `migrations/versions/0001_perf012.py` | Alembic 建表与索引；`alembic downgrade base → upgrade head` 已实测 |
| `app/main.py` | 三个列表接口默认 `pagination=cursor`；`pagination=offset` 保留为 deprecated 兼容模式（供灰度与对比），`ENABLE_OFFSET_BASELINE=0` 可关闭 |
| `docs/pagination.md` | 新分页参数、响应结构、错误码、offset→cursor 迁移表 |
| `docs/compression.md` | 压缩规则、协商方式、测量方法 |
| `docs/openapi.json` | 由 `scripts/export_openapi.py` 从运行中的应用导出，随代码更新 |
| `tests/` | 41 条测试：游标正确性/稳定性/租户隔离、压缩边界、OpenAPI 文档 |
| `scripts/run_benchmark.py` | 真实 HTTP 压测：原始字节体积、第 200 页 offset vs cursor、`EXPLAIN` 查询计划 |

## 3. 压缩策略

- 仅 `application/json` 与 `application/*+json`；
- 未压缩体积 `>= 1024` 字节才压缩；阈值为压缩前体积；
- `Accept-Encoding` 按 q 值协商，`br` 优先、`gzip` 次之；`identity` 始终可用；
- 已带 `Content-Encoding`、`Cache-Control: no-transform`、1xx/204/304、SSE 不处理；
- 所有 JSON 响应都带 `Vary: Accept-Encoding`，共享缓存不会混用压缩体与原文；
- 只缓冲同时带 `Content-Length` 且不超过 8 MB 的 JSON，避免把任意流式响应读进内存。

本次实测（PostgreSQL 16.16，100 条/页）：

| 接口 | 原始 | gzip | 节省 | br | 节省 |
|---|---:|---:|---:|---:|---:|
| members | 42,778 B | 4,365 B | 89.8% | 3,328 B | 92.2% |
| interactions | 69,560 B | 6,744 B | 90.3% | 5,293 B | 92.4% |
| audit_logs | 61,323 B | 5,851 B | 90.5% | 4,836 B | 92.1% |

低于 1 KB 的 `/api/v1/meta/dictionaries`（195 B）保持 `identity`，证明阈值生效。

## 4. 分页策略

三个接口的排序键：

| 接口 | 排序（倒序） | 复合索引 |
|---|---|---|
| `GET /api/v1/members` | `created_at, id` | `ix_members_org_created_id` |
| `GET /api/v1/interactions` | `occurred_at, id` | `ix_interactions_org_occurred_id` |
| `GET /api/v1/audit-logs` | `created_at, id` | `ix_audit_logs_org_created_id` |

请求示例：

```http
GET /api/v1/members?limit=50
→ 200 {"items":[...], "has_more":true, "next_cursor":"eyJ2Ijox..."}

GET /api/v1/members?limit=50&cursor=eyJ2Ijox...
→ 200 下一页
```

关键实现点：SQL 使用行比较 `WHERE (created_at, id) < (:ts, :id)`，而不是等价的
`created_at < :ts OR (created_at = :ts AND id < :id)`。后者只会作为 filter，PostgreSQL
仍会扫描所有更新的行；行比较会被下推进复合索引，扫描行数固定为 `limit+1`。
`EXPLAIN` 证据见 `evidence/benchmark-pg16.md`：offset 扫描 10,001 行，cursor 扫描 51 行。

第 200 页（limit=50，offset=9950）实测：

| 环境 | offset HTTP 中位数 | cursor HTTP 中位数 | HTTP 提速 | 数据库 EXPLAIN 提速 |
|---|---:|---:|---:|---:|
| PostgreSQL 16.16 | 1.80–1.97 ms | 1.24–1.32 ms | 1.45–1.50× | 58–79× |
| SQLite 3.50.4 | 2.36–2.75 ms | 1.03–1.11 ms | 2.29–2.48× | 索引条件含 `(sort,id)<(?,?)` |

> HTTP 数字含本机回环、事件循环与 FastAPI 序列化的固定约 1 ms 地板，所以端到端差距小于
> 数据库层差距；生产网络下数据库扫描行数的差异会更明显。所有数字是本地参考值，不是 SLA。

## 5. 运行与测试

### 5.1 默认路径（SQLite，零外部依赖）

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./scripts/test.sh        # = pytest -v --cov=app --cov-report=term-missing
```

- 默认数据库是 `.data/test-perf012.sqlite3`，每次运行前删除重建，不连接共享库。
- 应用默认用 `Base.metadata.create_all` 建表（`AUTO_CREATE_SCHEMA=1`），仅用于本地/测试。
- 生产用 Alembic：`alembic upgrade head`，并设置 `AUTO_CREATE_SCHEMA=0`。

### 5.2 PostgreSQL 16（已实跑）

```bash
# 任选一个隔离的 PG16 实例；下面是本次使用的独立 cluster
/opt/homebrew/opt/postgresql@16/bin/initdb -D .pgdata -U perf012 --auth=trust
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D .pgdata -o "-p 55433 -k /tmp" -l .pgdata/server.log start
/opt/homebrew/opt/postgresql@16/bin/createdb -h 127.0.0.1 -p 55433 -U perf012 perf012_test

export DATABASE_URL='postgresql+psycopg://perf012@127.0.0.1:55433/perf012_test'
export TEST_DATABASE_URL="$DATABASE_URL"
alembic upgrade head
./scripts/test.sh
```

测试夹具内置护栏：`TEST_DATABASE_URL` 必须是 `postgresql://`、host 必须显式写
`127.0.0.1/localhost/::1`、库名必须含 `test/bench/perf012`；任一不满足直接失败。
本次实测：PostgreSQL 16.16，41 passed，覆盖率 90%。

### 5.3 压缩与第 200 页基准

```bash
# 1) 准备隔离基准库（脚本会拒绝重置不含 bench/test/perf012 的库）
export DATABASE_URL='postgresql+psycopg://perf012@127.0.0.1:55433/perf012_bench'
alembic upgrade head
python scripts/seed_benchmark.py --reset \
  --members 40000 --interactions 80000 --audits 120000

# 2) 跑基准（脚本自动起 uvicorn、结束自动停）
export CURSOR_SIGNING_KEY='bench-only-key'   # 必须与应用一致
python scripts/run_benchmark.py --port 8031 --repeats 30 \
  --output evidence/benchmark-pg16.json
```

- SQLite 版本：把 `DATABASE_URL` 换成 `sqlite+pysqlite:///./.data/bench.sqlite3` 后重复即可。
- 输出同时包含 JSON 与 Markdown；Markdown 表格可直接贴进评审材料。
- 端口被占用时换 `--port`；`--base-url` 可复用已启动的服务。

## 6. 接口文档

- `docs/pagination.md`：新分页参数、响应字段、错误码、迁移表；
- `docs/compression.md`：压缩规则与测量方法；
- `docs/openapi.json`：`python scripts/export_openapi.py` 生成，`tests/test_openapi.py` 校验；
- 运行时交互文档：`GET /docs`，OpenAPI：`GET /openapi.json`。

错误码：

| HTTP | code | 含义 |
|---|---|---|
| 400 | `INVALID_CURSOR` | 游标格式错/签名被篡改 |
| 400 | `CURSOR_SCOPE_MISMATCH` | 游标与当前租户或筛选条件不一致 |
| 400 | `OFFSET_NOT_ALLOWED` | cursor 模式下传了非 0 offset |
| 400 | `CURSOR_NOT_ALLOWED` | offset 模式下传了 cursor |
| 400 | `OFFSET_PAGINATION_DISABLED` | 服务端已关闭 offset 兼容模式 |
| 422 | — | `limit` 越界或 UUID 参数非法 |

## 7. 对接真实服务

| 本仓库 | 生产替换点 |
|---|---|
| `app/identity.py` 的 `X-Org-Id` | 由 S2-IAM 的登录态解析 `org_id/site_id/user_id` |
| `Member` 桩表 | S2-CRM 的会员查询服务/视图；保持 `created_at,id` 排序与同样的复合索引 |
| `Interaction` / `AuditLog` 桩表 | 互动服务/审计服务；同样保留 `org_id, 排序键, id` 索引 |
| `CURSOR_SIGNING_KEY` 环境变量 | 生产密钥管理系统注入，禁止使用仓库默认值 |
| 应用内压缩 | 若 Ingress/Nginx 已开启 gzip/br，关闭其中一层，避免重复压缩 |

## 8. 测试与证据索引

| 文件 | 内容 |
|---|---|
| `evidence/test-results.txt` | SQLite：41 passed，覆盖率 91% |
| `evidence/test-results-postgres.txt` | PostgreSQL 16.16：41 passed，覆盖率 90% |
| `evidence/benchmark-pg16.json/.md` | PG16 压缩体积、第 200 页 HTTP、`EXPLAIN`、查询计划 |
| `evidence/benchmark-sqlite.json/.md` | SQLite 同口径结果与索引计划 |
| `evidence/migration-check.txt` | `downgrade base → upgrade head` 与表/索引清单 |
