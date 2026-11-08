# Agent 网关与 LLM 中间件（W3）

> 放在 Agent 运行时与模型供应商之间的统一出口。FastAPI 只做 HTTP 适配，
> 路由 / 缓存 / 限流 / 幂等 / 成本核算全部是零第三方依赖、可单测的核心逻辑。
> **评测装置 `agent-reliability-harness`（P1）是负载来源，本仓库是独立新仓库、独立 commit 历史，不修改 P1 任何文件。**

## 30 秒看懂

| 问题 | 回答 |
| --- | --- |
| 是什么 | 一层 OpenAI 兼容的模型调用出口，不是 Agent 应用、不是评测平台 |
| 做了什么 | 四能力做深 + 单进程并发队列 + 完整 SSE + 可选容器复现 / 负载不变量 / trace 视图 |
| 怎么证明在跑 | 串行 off/on 单变量对照归档三件；并发不变量自检；归档只读视图 |
| 花了多少钱 | 默认路径零 API 成本、零网络调用 |
| 能对外说什么 | 「四条决策链路各自可测」「并发下计数不丢、背压生效」「归档可确定性回放」 |
| 不能说什么 | 生产级网关、通用 Agent 治理、上线后能省钱、真实 p95、压测 QPS |

## 是什么

W1 选 4 个能力做深（参考 LiteLLM / Portkey / Cloudflare AI Gateway 的能力边界，不做全）：

| 能力 | 决策点 | 不做 |
| --- | --- | --- |
| 模型路由 + fallback | 主上游失败按固定顺序降级 | 延迟感知路由、多活 |
| 精确缓存 + LRU/逻辑 TTL | 规范化请求 key 命中即返回 | 语义缓存、跨实例共享 |
| 固定窗口限流 | 按 caller 计数，超额 429 | 分布式限流、令牌桶 |
| 幂等键 | 同 key 重放首次结果、不重复计费 | 跨实例锁、过期策略 |

横切能力：每次请求记一条可归档 span（token、单价、模拟延迟、命中链路、失败原因）。
默认上游是**确定性 fake**，零 API 成本、零网络调用、逐字节可复现。

W2 新增三块（详见 [docs/design-w2.md](docs/design-w2.md)）：

- **并发 / 队列**：单进程有界容量闸门 + 线程池 + 同幂等键 single-flight 去重，
  纯标准库，不引 Redis / MQ / K8s（R6）。四个共享组件各自带 `RLock`。
- **SSE 完整语义**：role 首帧、可配置分块、尾帧带 `finish_reason` 与 `usage`、
  `[DONE]` 终止、`event: error` 错误帧、`event: cancelled` 取消帧。
- **真实供应商适配器**：OpenAI 兼容 `chat/completions` 的最小 HTTP 上游（`urllib`），
  **默认关闭、不进基准、不改 `config_hash`**；密钥只从环境变量读。

W3 新增三块（详见 [docs/design-w3.md](docs/design-w3.md)）：

- **可选容器复现**：`Dockerfile` + `docker-compose.yml`（`gateway` 起服务 / `verify` 跑离线验收）。
  **不是复现前提** —— 主路径仍是裸 Python 的全量测试与归档复验，容器只是另一种一键起服务方式。
  本机无 Docker，本轮**未实跑** `build` / `compose up`，只交付静态清单与守门测试。
- **负载生成器**：`gateway/loadgen.py`，可配参数的并发负载，只产与调度无关的不变量
  （上游调用数、限流计数、台账条数、闸门容量），**不测 QPS / p95 / 墙钟耗时**。
- **trace 回放视图**：`gateway/trace.py`，只读归档生成自包含静态 HTML（零 JS / 零外链），
  同归档逐字节相同；示例视图随仓库入库，`--check` 可复验。

## 两条互不相交的路径（最重要）

| 路径 | 入口 | 产出 | 是否进归档 |
| --- | --- | --- | --- |
| 串行基准 | `python -m gateway.replay` | off/on 单变量对照，逐字节可复现 | 是（三件） |
| 并发执行 | `gateway.concurrency.ConcurrentGateway` | 并发不变量 | **否** |

并发下「谁先命中缓存」由线程调度决定，成本与命中数字不可复现。所以并发路径
**不产出 `cache_hit_rate` / `cost_saving` / `p95_ms`，也不与 W1 归档数字相减或拼曲线**。
W1 的 `GatewayEngine.handle()` 一行未改、`GatewayConfig` 未加字段，两组 W1 归档逐字节不变。

## 怎么跑

核心基准、并发自检、负载生成、trace 视图与全量测试**不需要装任何依赖**：

```bash
python -m unittest discover -s tests -q
python -m gateway.replay --n 600 --seed 11
python -m gateway.bench
python -m gateway.loadgen --n 64
python -m gateway.trace --run replay-aa1142308d8d
python -m gateway.trace --run replay-aa1142308d8d --check
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

可选容器（需 Docker，本轮未实跑）：

```bash
docker compose up --build -d gateway
docker compose --profile verify run --rm verify
```

端点：`POST /v1/chat/completions`（OpenAI 兼容子集，支持 `stream=true` 的 SSE）、
`GET /health`、`GET /metrics`（含并发统计）、`GET /admin/config`。

## 关键结论

`python -m gateway.replay` 做 **off（完全直通）/ on（四能力全开）单变量对照**，
归档到 `experiments/runs/replay-<config_hash>/` 三件
（`report.md` / `config.json` / `decisions.jsonl`）。缓存命中、幂等重放、
限流拒绝、降级触发四类事件各自被计数，并给出相对成本差。
**这只证明「四条决策链路确实在跑且各自可测」，不证明上线后能省钱。**

`python -m gateway.bench` 只验证并发不变量，不测性能：
32 个并发同幂等键请求 → 上游**恰好调用 1 次**、台账 32 条不丢；
40 个并发请求在 `rpm_limit=3` 下 → 200 恰好 3 个、429 恰好 37 个、台账 40 条。
这些计数与线程调度无关，因此可复现；**不是 QPS、不是 SLA、不外推生产吞吐**。

`python -m gateway.loadgen` 是可配参数的推广：同键并发 → 上游恰好 1 次；
`rpm_limit=3` 下 200 恰 3、429 恰 N−3；纯闸门容量 C → 接受恰 C、拒绝恰 M。
用 `BlockingUpstream` 阻塞首个上游调用**真正打开并发窗口**（W3 实测修正：
初版 `single_flight_waited` 恒为 0，自检退化成串行回放）。
`queue_rejected` / `waited` 依赖线程调度，**只输出、不断言**。

`python -m gateway.trace` 从 `decisions.jsonl` **全量复算** off/on 汇总并画出决策链路
时间线，不新增指标、不改归档一个字节；`--check` 验证同归档渲染逐字节相同，
并与已提交的示例视图比对。

## 边界

- 全部默认上游是确定性 fake，`p95_ms` 是 **SHA256 派生的模拟延迟**，
  不是网络延迟、不是生产 SLA；报告不记录墙钟耗时。
- 缓存命中率主要由 `repeat_rate` 决定，它是**自变量**不是优化成果；
  单价表写死，成本差只反映「命中不计费」与「降级用更贵模型」两条机制。
- fake 失败按 `failure_rate` 注入，不代表供应商真实可用性；固定窗口有边界效应。
  请求内容参与上游派生，换负载会改变 token / 延迟分布。
- `config_hash` 只锁定配置、不锁定代码；完整复现条件是「同配置 + 同一 git commit」。
- **不是通用 Agent 评测平台，不接任意外部 Agent**；只实现 OpenAI 兼容
  `chat/completions` 的一个子集，不是生产级网关。SSE 不支持 tool_calls 增量 / 多 choice。
- 并发是**单进程内**的，不做分布式限流 / 跨实例缓存；缓存并发 miss 允许重复调上游
  （只有幂等键做 single-flight）。并发台账的写入顺序由调度决定，故不产出归档行。
- 真实供应商适配器默认关闭，启用后**不采集真实延迟**（避免与模拟延迟字段混用），
  也不写进任何现有报告口径。与 P1 的关系仍是「只读任务集做回放负载」，**不是运行时直连**。
- W3 的容器只是**可选便利**：不引入 Redis / Postgres / MQ / K8s，镜像只装已有的 3 个包，
  不跑多副本；本机无 Docker，**未实跑 build / compose**，只做静态清单与守门测试。
- loadgen 是**并发不变量自检，不是性能基准**；不产 QPS / p95 / 墙钟，不写归档行，
  不与 W1 数字相减。trace 视图是归档的**只读可视化**，不是实时链路追踪 / 可观测平台；
  时间线默认每格展示前 48 条，统计基于全量 1200 行。
- W4（挂 P1 运行时直连）只产真实链路可观测事件与成本/延迟，**不产 `pass^k` 正向结论**。

## 证据索引

- `docs/design.md` — W1 红线 R1–R6、四能力、指标定义、不许说的话
- `docs/design-w2.md` — W2 范围、两条路径取舍、并发模型、生命周期修正记录
- `docs/design-w3.md` — W3 三条边界、并发窗口实测修正、容器未实跑说明
- `bridge/README.md` — 如何挂 P1 负载、W4 直连步骤与红线
- `experiments/runs/` — 回放归档三件（**随仓库入库**，`clone` 即可复查）
- `experiments/views/replay-aa1142308d8d.html` — 示例 trace 视图（可逐字节复验）
- `tests/` — 157 条 unittest；核心零依赖，`test_app.py` 在装了 fastapi 时才跑

## 阶段

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 设计写死、四能力核心、回放基准、FastAPI 薄适配、单测 | 已交付 |
| W2 | 并发 / 容量闸门 / single-flight、SSE 完整语义、可选真实供应商适配器、并发不变量 | 已交付 |
| W3 | 可选容器复现、负载不变量、trace 回放视图 | 本轮交付 |
| W4 | 挂 P1 harness 直连跑真实对照（需用户本人执行与 API 预算） | 未做 |
| W5–W6 | README 打磨、简历 bullet、面试追问预案 | 未做 |

每阶段一个 commit、可随时停，不自动扩张。许可证：MIT。
