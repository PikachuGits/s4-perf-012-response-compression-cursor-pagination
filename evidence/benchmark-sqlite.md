# S4-PERF-012 实测结果

- 生成时间：2026-11-21T13:22:50+00:00
- 数据库：sqlite 3.50.4
- Python：3.12.12；重复次数：30

## 1. JSON 压缩（100 条/页）

| 接口 | 原始体积 | gzip | 节省 | br | 节省 |
|---|---:|---:|---:|---:|---:|
| members | 41,578 B | 4,346 B | 89.5% | 3,322 B | 92.0% |
| interactions | 67,760 B | 6,737 B | 90.1% | 5,262 B | 92.2% |
| audit_logs | 60,123 B | 5,863 B | 90.2% | 4,808 B | 92.0% |

低于 1 KB 的对照接口（/api/v1/meta/dictionaries，195 B）的 `Content-Encoding` 为 `identity`，未被压缩。

## 2. 第 200 页响应时间（limit=50）

| 接口 | offset HTTP 中位数 | 游标 HTTP 中位数 | 提速 | offset P95 | 游标 P95 |
|---|---:|---:|---:|---:|---:|
| members | 2.36 ms | 1.03 ms | 2.29× | 2.71 ms | 1.21 ms |
| interactions | 2.75 ms | 1.11 ms | 2.48× | 3.07 ms | 1.45 ms |
| audit_logs | 2.61 ms | 1.10 ms | 2.38× | 2.90 ms | 1.31 ms |

> HTTP 列是端到端响应时间（含连接复用、FastAPI 序列化与事件循环开销），固定地板约 1 ms。offset 模式的 `total` 未计入（`with_total` 单独测量），以保证对比只反映分页方式本身。

### 2.1 数据库层对比（排除 HTTP 地板）

SQLite 的 `EXPLAIN QUERY PLAN` 不提供执行耗时，因此用查询计划直接对比：游标查询的索引条件包含 `(排序键, id) < (?, ?)`，offset 查询只有 `org_id=?`。查询计划见 2.2。

### 2.2 查询计划关键行

| 接口 | offset | 游标 |
|---|---|---|
| members | `SEARCH members USING INDEX ix_members_org_created_id (org_id=?)` | `SEARCH members USING INDEX ix_members_org_created_id (org_id=? AND (created_at,id)<(?,?))` |
| interactions | `SEARCH interactions USING INDEX ix_interactions_org_occurred_id (org_id=?)` | `SEARCH interactions USING INDEX ix_interactions_org_occurred_id (org_id=? AND (occurred_at,id)<(?,?))` |
| audit_logs | `SEARCH audit_logs USING INDEX ix_audit_logs_org_created_id (org_id=?)` | `SEARCH audit_logs USING INDEX ix_audit_logs_org_created_id (org_id=? AND (created_at,id)<(?,?))` |
