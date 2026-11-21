# JSON 响应压缩说明

## 规则

- 仅压缩 `application/json` 与 `application/*+json`；
- 未压缩体积 `>= 1024` 字节才压缩（阈值为压缩前体积）；
- 按 `Accept-Encoding` 的 q 值协商，优先 `br`，其次 `gzip`；
- `identity;q=0` 且没有可接受编码时不压缩（返回原始 JSON，不返回 406，避免影响监控探针）；
- 已有 `Content-Encoding`、`Cache-Control: no-transform`、1xx/204/304、SSE 不处理；
- 所有 JSON 响应都带 `Vary: Accept-Encoding`，避免共享缓存把压缩体与原文串用；
- 压缩后设置 `Content-Length`，并附加 `X-Uncompressed-Length` 便于观测压缩比。

## 客户端示例

```bash
# br（服务端安装 brotli 时）
curl -H 'Accept-Encoding: br' -H 'X-Org-Id: 11111111-1111-7111-8111-111111111111' \
  'http://127.0.0.1:8021/api/v1/members?limit=100' -o members.br

# gzip
curl -H 'Accept-Encoding: gzip' ... -o members.gz
```

## 实测

`scripts/run_benchmark.py` 用 `http.client` 读取**原始字节**（不会被客户端自动解压），
对同一接口分别请求 `identity` / `gzip` / `br`，记录原始体积、压缩体积、压缩比与耗时。
结果写入 `evidence/benchmark-pg16.json`/`benchmark-sqlite.json`，表格见对应的 `.md`。
