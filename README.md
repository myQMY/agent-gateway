# Agent 网关与 LLM 中间件（W1 骨架）

> 放在 Agent 运行时与模型供应商之间的统一出口。FastAPI 只做 HTTP 适配，路由 / 缓存 / 限流 / 幂等 / 成本核算全部是零第三方依赖、可单测的核心逻辑。
> **评测装置 `agent-reliability-harness`（P1）是负载来源，本仓库是独立新仓库、独立 commit 历史，不修改 P1 任何文件。**

## 是什么

选 4 个能力做深（参考 LiteLLM / Portkey / Cloudflare AI Gateway 的能力边界，不做全）：

| 能力 | 决策点 | 不做 |
| --- | --- | --- |
| 模型路由 + fallback | 主上游失败按固定顺序降级 | 延迟感知路由、多活 |
| 精确缓存 + LRU/逻辑 TTL | 规范化请求 key 命中即返回 | 语义缓存、跨实例共享 |
| 固定窗口限流 | 按 caller 计数，超额 429 | 分布式限流、令牌桶 |
| 幂等键 | 同 key 重放首次结果、不重复计费 | 跨实例锁、过期策略 |

横切能力：每次请求记一条可归档 span（token、单价、模拟延迟、命中链路、失败原因）。
默认上游是**确定性 fake**，零 API 成本、零网络调用、逐字节可复现；真实供应商适配器属 W2，本轮未做。

## 怎么跑

核心基准与全量测试**不需要装任何依赖**：

```bash
python -m unittest discover -s tests -q
python -m gateway.replay --n 600 --seed 11
python -m bridge.p1_replay --tasks "D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl" --n 600
```

起 HTTP 服务才需要 FastAPI：

```bash
pip install -r requirements.txt
uvicorn gateway.app:app --port 8000
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gateway-primary","messages":[{"role":"user","content":"hello"}]}'
```

端点：`POST /v1/chat/completions`（OpenAI 兼容子集，支持 `stream=true` 的 SSE）、`GET /health`、`GET /metrics`、`GET /admin/config`。

## 关键结论

`python -m gateway.replay` 做 **off（完全直通）/ on（四能力全开）单变量对照**，归档到 `experiments/runs/replay-<config_hash>/` 三件（`report.md` / `config.json` / `decisions.jsonl`）。报告里能看到缓存命中、幂等重放、限流拒绝、降级触发四类事件各自被计数，以及相对成本差。

**这只证明「四条决策链路确实在跑且各自可测」，不证明上线后能省钱。** 具体数字看报告，不在 README 里写死以免过期。

## 边界

- 全部上游是确定性 fake，`p95_ms` 是 **SHA256 派生的模拟延迟**，不是网络延迟、不是生产 SLA；报告不记录墙钟耗时。
- 缓存命中率主要由配置里的 `repeat_rate` 决定，它是**自变量**不是优化成果；单价表写死，成本差只反映「命中不计费」与「降级用更贵模型」两条机制。
- fake 失败按 `failure_rate` 注入，不代表供应商真实可用性；固定窗口以请求序号划分逻辑窗口，边界效应如实存在。请求内容参与上游派生，所以换负载会改变 token / 延迟分布，不是换个名字的同一组数字。
- `config_hash` 只锁定配置、不锁定代码；完整复现条件是「同配置 + 同一 git commit」。
- **不是通用 Agent 评测平台，不接任意外部 Agent**；只实现 OpenAI 兼容 `chat/completions` 的一个子集，不是生产级网关。
- 与 P1 的关系目前是「只读任务集做回放负载」，**不是运行时直连**；后者（真实模型 + 真实成本）属 W4，需用户本人执行与 API 预算，本轮未做。
- 单进程、非并发安全；并发与队列属 W2。

## 证据索引

- `docs/design.md` — 跑代码之前写死的范围、红线、指标定义、阶段与停止条件
- `bridge/README.md` — 如何挂 P1 负载、W4 直连步骤与红线
- `experiments/runs/` — 回放归档三件（**随仓库入库**，`clone` 下来即可复查；`config_hash` 可核对同配置逐字节一致）
- `tests/` — unittest 行为测试；核心零依赖，`test_app.py` 在装了 fastapi 时才跑

## 阶段

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 设计写死、四能力核心、回放基准、FastAPI 薄适配、单测、单 commit | 本轮交付 |
| W2 | 并发 / 队列、SSE 完整实现、真实供应商适配器 | 未做 |
| W3 | Docker Compose 一键复现、压测、trace 回放 | 未做 |
| W4 | 挂 P1 harness 直连跑真实对照 | 未做 |
| W5–W6 | README 打磨、简历 bullet、面试追问预案 | 未做 |

本轮只交付 W1 可跑骨架，不假装做完整 5–6 周项目。后续按实际可投入时间裁剪，不自动扩张。

许可证：MIT。
