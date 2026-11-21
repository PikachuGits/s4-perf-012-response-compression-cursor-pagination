# S4-PERF-012 实测结果

- 生成时间：2026-11-21T13:23:00+00:00
- 数据库：postgresql 16.16 (Homebrew)
- Python：3.12.12；重复次数：30

## 1. JSON 压缩（100 条/页）

| 接口 | 原始体积 | gzip | 节省 | br | 节省 |
|---|---:|---:|---:|---:|---:|
| members | 42,778 B | 4,365 B | 89.8% | 3,328 B | 92.2% |
| interactions | 69,560 B | 6,744 B | 90.3% | 5,293 B | 92.4% |
| audit_logs | 61,323 B | 5,851 B | 90.5% | 4,836 B | 92.1% |

低于 1 KB 的对照接口（/api/v1/meta/dictionaries，195 B）的 `Content-Encoding` 为 `identity`，未被压缩。

## 2. 第 200 页响应时间（limit=50）

| 接口 | offset HTTP 中位数 | 游标 HTTP 中位数 | 提速 | offset P95 | 游标 P95 |
|---|---:|---:|---:|---:|---:|
| members | 1.80 ms | 1.24 ms | 1.45× | 2.02 ms | 1.60 ms |
| interactions | 1.97 ms | 1.32 ms | 1.49× | 2.23 ms | 1.57 ms |
| audit_logs | 1.93 ms | 1.29 ms | 1.50× | 2.23 ms | 1.66 ms |

> HTTP 列是端到端响应时间（含连接复用、FastAPI 序列化与事件循环开销），固定地板约 1 ms。offset 模式的 `total` 未计入（`with_total` 单独测量），以保证对比只反映分页方式本身。

### 2.1 数据库层对比（排除 HTTP 地板）

| 接口 | offset EXPLAIN | 游标 EXPLAIN | EXPLAIN 提速 | offset 查询往返 | 游标查询往返 |
|---|---:|---:|---:|---:|---:|
| members | 0.993 ms | 0.017 ms | 58× | 0.82 ms | 0.13 ms |
| interactions | 0.946 ms | 0.012 ms | 79× | 0.90 ms | 0.12 ms |
| audit_logs | 1.005 ms | 0.015 ms | 67× | 0.83 ms | 0.17 ms |

> PostgreSQL 的 EXPLAIN 值是 `EXPLAIN (ANALYZE, BUFFERS)` 的 Execution Time；查询往返值是同一 SQL 经 SQLAlchemy 执行的中位数（含驱动与编译开销）。

### 2.2 查询计划关键行

| 接口 | offset | 游标 |
|---|---|---|
| members | `->  Index Scan Backward using ix_members_org_created_id on members  (cost=0.41..4174.60 rows=40000 width=24) (actual time=0.002..0.824 rows=10001 loops=1)` | `->  Index Scan Backward using ix_members_org_created_id on members  (cost=0.41..3645.32 rows=29967 width=24) (actual time=0.003..0.007 rows=51 loops=1)` |
| interactions | `->  Index Scan Backward using ix_interactions_org_occurred_id on interactions  (cost=0.42..13452.04 rows=80000 width=24) (actual time=0.002..0.781 rows=10001 loops=1)` | `->  Index Scan Backward using ix_interactions_org_occurred_id on interactions  (cost=0.42..12885.57 rows=70189 width=24) (actual time=0.002..0.007 rows=51 loops=1)` |
| audit_logs | `->  Index Scan Backward using ix_audit_logs_org_created_id on audit_logs  (cost=0.42..17628.73 rows=120000 width=24) (actual time=0.002..0.835 rows=10001 loops=1)` | `->  Index Scan Backward using ix_audit_logs_org_created_id on audit_logs  (cost=0.42..17202.67 rows=110047 width=24) (actual time=0.003..0.009 rows=51 loops=1)` |
