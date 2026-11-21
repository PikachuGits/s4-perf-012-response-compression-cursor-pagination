# 列表接口分页参数（游标分页）

本文是 S4-PERF-012 的接口文档变更页。三个深分页列表接口的默认分页方式已从
`limit/offset` 改为**签名游标（keyset）分页**：

| 接口 | 排序键（倒序） | 说明 |
|---|---|---|
| `GET /api/v1/members` | `created_at, id` | 会员列表 |
| `GET /api/v1/interactions` | `occurred_at, id` | 互动记录 |
| `GET /api/v1/audit-logs` | `created_at, id` | 审计查询 |

## 1. 请求参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 50 | 每页条数，范围 1–200 |
| `cursor` | string | 无 | 上一页返回的 `next_cursor`；首屏不传，必须原样回传 |
| `pagination` | enum | `cursor` | `pagination=cursor`（推荐）或 `pagination=offset`（已废弃，仅灰度/对比期） |
| `offset` | int | 0 | **已废弃**，仅 `pagination=offset` 时生效 |
| `with_total` | bool | false | 仅 offset 模式：额外执行 COUNT 返回 `total`，会增加耗时 |

筛选参数仍然可用；游标会绑定当前筛选条件与租户：

- members：`status`、`tier`、`q`（名称/企业名模糊搜索）
- interactions：`member_id`、`channel`
- audit-logs：`action`、`actor_id`、`outcome`

租户由 `X-Org-Id` 请求头决定（参考实现；生产由登录态解析）。

## 2. 响应结构

```json
{
  "items": [],
  "limit": 50,
  "has_more": true,
  "pagination": "cursor",
  "next_cursor": "eyJ2IjoxLCJzIjoi...0Y2Fi",
  "offset": null,
  "next_offset": null,
  "total": null
}
```

- `next_cursor` 仅在 `has_more=true` 时返回；把它原样放进下一次请求的 `cursor`。
- 游标是**不透明字符串**，不要解析、拼接、截断或跨环境复用。
- 页码不再由服务端计算；需要“第 200 页”时，客户端顺序翻 199 次，或由业务侧保存每一页的
  `next_cursor`。这正是消除深偏移扫描的方式。

offset 兼容模式额外返回 `offset`、`next_offset`，`with_total=1` 时返回 `total`：

```json
{
  "items": [],
  "limit": 50,
  "has_more": true,
  "pagination": "offset",
  "next_cursor": null,
  "offset": 9950,
  "next_offset": 10000,
  "total": 40000
}
```

## 3. 错误码

| HTTP | `detail.code` | 触发条件 | 客户端处理 |
|---|---|---|---|
| 400 | `INVALID_CURSOR` | 格式错误、签名被篡改、版本不支持 | 丢弃游标，从第一页重新拉取 |
| 400 | `CURSOR_SCOPE_MISMATCH` | 游标对应的租户/筛选条件与当前请求不一致 | 清空旧游标，按新条件从第一页开始 |
| 400 | `OFFSET_NOT_ALLOWED` | cursor 模式下传了非 0 的 `offset` | 去掉 `offset`，改用 `cursor` |
| 400 | `CURSOR_NOT_ALLOWED` | offset 模式下传了 `cursor` | 二选一 |
| 400 | `OFFSET_PAGINATION_DISABLED` | 服务端已关闭 offset 兼容模式 | 升级到游标分页 |
| 422 | — | `limit` 越界、UUID 筛选值非法 | 按校验提示修正 |

错误响应示例：

```json
{"detail": {"code": "CURSOR_SCOPE_MISMATCH", "message": "游标与当前租户或筛选条件不匹配，请在当前筛选条件的第一页重新开始"}}
```

## 4. 从 offset 迁移

| 旧调用 | 新调用 |
|---|---|
| `GET /api/v1/members?limit=50&offset=0` | `GET /api/v1/members?limit=50` |
| 下一页 `offset=50` | 使用上一页响应里的 `next_cursor` |
| 第 200 页 `offset=9950` | 顺序翻页拿到第 199 页的 `next_cursor` 后请求一次；或直接保存游标 |

迁移期可以同时传 `pagination=offset` 做灰度对比，但新客户端不要依赖它；兼容模式将在灰度结束后移除。

## 5. 为什么第 200 页更快

offset 查询必须先扫描并丢弃 `offset` 行，再取 `limit` 行；游标查询把
`(created_at, id)` 作为边界，直接在 `(org_id, created_at, id)` 索引上做范围扫描，
扫描行数与页深无关。仓库的 `scripts/run_benchmark.py` 会对第 200 页做真实 HTTP 对比，
并用 `EXPLAIN` 记录扫描计划；实测数字见 `evidence/benchmark-pg16.md` 与 `evidence/benchmark-sqlite.md`。

## 6. 缓存与压缩

列表响应的 `Vary: Accept-Encoding` 由压缩中间件统一设置。大于 1 KB 的 JSON 会按
`Accept-Encoding` 返回 br 或 gzip；`next_cursor` 本身也会被压缩，不影响语义。
