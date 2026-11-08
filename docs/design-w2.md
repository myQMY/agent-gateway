# W2 设计补充：并发 / 队列 / SSE / 真实供应商适配器

> 本文件在写任何 W2 实现代码之前写死。`docs/design.md` 的 R1–R6 红线全部继续有效，
> 本文件只增补 W2 的范围、自变量、指标定义与边界，不替换原文。
> 日期：2026-11-08　仓库：`agent-gateway`　阶段：W2

## 0. 本轮补什么

上一轮审计列出的缺口里，W2 只补三块：**并发 / 任务队列**、**SSE 完整语义**、**真实供应商适配器**。
Docker Compose、压测可视化、trace 回放视图仍属 W3；挂 P1 运行时直连属 W4。

不补的东西同样写死：不做分布式限流、不做跨实例缓存、不做语义缓存、不做前端、
不引入 Redis / Postgres / K8s / MQ 作为依赖（R6），不加任何第三方 Python 包。

## 1. 最重要的取舍：并发路径不产出对照数字

W1 的 `run_pair` 是逐字节可复现的单变量 off/on 对照。它的正确性依赖「请求严格按
`index` 串行处理」：限流窗口、缓存 LRU 与逻辑 TTL、幂等表的命中结果都由**处理顺序**
决定。一旦并发，「谁先命中缓存」由线程调度决定，成本与命中数字就不再可复现。

因此 W2 明确分成两条互不相交的路径：

| 路径 | 入口 | 用途 | 是否进归档 |
| --- | --- | --- | --- |
| 串行基准 | `gateway.replay.run_pair` | off/on 单变量对照，逐字节可复现 | 是（三件，与 W1 完全相同） |
| 并发执行 | `gateway.concurrency.ConcurrentGateway` | 服务层并发处理 + 背压 | **否**；只产出并发不变量 |

**硬约束**：

- W1 的 `GatewayEngine.handle()` 逻辑一行不改；`replay.py` 不 import 任何 W2 模块。
- `GatewayConfig` **不新增任何字段**：`config_hash()` 与 W1 两组归档编号必须逐字节不变。
  真实供应商配置走独立的 `RealUpstreamConfig`，不进配置摘要、不进任何归档。
- 并发路径绝不产出 `cache_hit_rate` / `cost_saving` / `p95_ms` 这类对照数字，
  也不与 W1 归档数字相减或拼曲线。它只证明「并发下计数不丢、状态不坏、背压生效」。
- 墙钟耗时只在命令行打印供本地诊断，**不进任何归档字段**（沿用 W1 的 `wall_ms` 纪律）。

## 2. 并发模型（受 R6 约束）

单进程内 `threading` + `queue.Queue` + `concurrent.futures.ThreadPoolExecutor`，
全部标准库。不做多进程、不做跨实例协调。

```
submit(req) -> Future[Decision]
    |
    +-- 有界容量闸门（capacity = queue_capacity），限制「处理中 + 等待调度」
    |   的在途请求数；满则立即拒绝
    |      拒绝 -> Decision(status=429, reasons=("queue_full",))
    |
    +-- 每个请求作为 ThreadPoolExecutor 的一个**短任务**提交（workers 个线程）
    |      真正的排队由 executor 内部队列承担
    |      同 idempotency_key 的并发请求做 single-flight 去重：
    |      首个调用上游，其余等待首个完成后重放，不重复计费
    |
    +-- 结果按 request.index 排序后交给调用方（仅为稳定输出顺序，
           不声称并发结果与串行结果数值相同）
```

**实现注记（W2 实测修正过的一个真实缺陷）**：初版用「常驻 worker 循环 +
自建 queue.Queue」，worker 作为 executor 的长期任务一直占住线程不返回。解释器退出时
`concurrent.futures.thread._python_exit` 往 executor **内部**队列投关闭哨兵，但常驻
worker 阻塞在自建队列上、永远不消费它，`join()` 永久挂住，进程无法退出。改为
「容量闸门 + 每请求一个短任务」后，executor 能正常收尾。面试时这点值得主动讲：
它说明并发层的生命周期是验证过的，不是只跑通了功能。

**为什么需要 single-flight**：幂等键的「查 → 调上游 → 写回」若不是原子的，
N 个并发同 key 请求会全部穿透到上游，幂等就失效了。这是并发层唯一必须实现的
跨请求协调能力；缓存并发 miss 允许重复调用上游（真实网关常见行为），如实写明。

**并发安全**：`ExactCache` / `FixedWindowLimiter` / `IdempotencyStore` / `Ledger`
各自用 `RLock` 保护自身数据结构。加锁不改变串行路径的任何行为，所以 W1 归档不变。
上游调用发生在锁外，因此 I/O 可以真正并行，不是「一把大锁把并发度压成 1」。

**可复现的并发不变量**（与线程调度无关，可作为断言与证据）：

| 不变量 | 定义 | 为什么确定 |
| --- | --- | --- |
| `single_flight_calls` | 被 single-flight 选为 leader 的请求数 | leader 可能命中缓存 / 幂等表而不再调上游；「上游恰好 1 次」由 `CountingUpstream` 单独验证 |
| `rate_limited_count` | limit=L 时，200 响应数恰好 = L | 限流计数原子，与调度无关 |
| `ledger_count` | 完成的请求数 = 台账 span 数 | 计数不丢，与调度无关 |
| `queue_rejected` | 超出有界队列容量的提交数 | 队列容量是配置自变量 |

**台账口径**：被队列拒绝的请求不是「已完成的请求」，**不写进台账 span**，
只在 `queue_rejected` 里计数。并发下台账 span 的**写入顺序**由线程调度决定，
所以并发路径不产出 `decisions.jsonl`，也不与 W1 归档的数字相减。

**不记录为指标**：吞吐 QPS、真实 p95、墙钟延迟、线程竞争耗时。这些依赖机器与调度，
写进报告就会变成无法复现的数字（R4 / R5）。

## 3. SSE 完整语义

W1 只有「按 16 字符切块 + `[DONE]`」。W2 补齐 OpenAI 兼容子集的流式语义：

- 首个 chunk 带 `delta: {"role": "assistant"}`；
- 内容按可配置 `chunk_size` 切块，`content` 拼接后必须还原原文；
- 终止 chunk 带 `finish_reason: "stop"` 与 `usage`；
- 固定以 `data: [DONE]` 结尾；
- 非 200 决策输出 `event: error` 帧后再 `[DONE]`，而不是降级成普通 JSON；
- 支持取消（调用方传入 `is_cancelled`），取消时发 `event: cancelled` 并停止，
  **不再发 `[DONE]`**；
- 响应头带 `Cache-Control: no-cache` / `X-Accel-Buffering: no`。

SSE 帧生成放在纯标准库模块 `gateway/sse.py`，`gateway/app.py` 只做
`StreamingResponse` 包装与断线检测。缓存命中 / 幂等重放同样走流式输出，
不因命中而跳过事件序列。

**边界**：仍是 `chat/completions` 的一个子集，不支持 tool_calls 增量、
不支持 logprobs、不支持多 choice；**不宣称兼容全部 OpenAI 参数**（R2）。

## 4. 真实供应商适配器（可选，默认关闭）

`gateway/real_upstream.py` 提供 OpenAI 兼容 `chat/completions` 的最小 HTTP 上游，
用 `urllib.request` 实现，零第三方包。

- 配置独立为 `RealUpstreamConfig(base_url, api_key_env, timeout_s)`；
  密钥只从环境变量名读取，**不写进配置文件、不写进归档、不写进日志**。
- **默认 `None`**：不显式注入时，`create_app` / `GatewayEngine` 一律用 `FakeUpstream`。
- **不进基准**：`replay.py` 与 `run_pair` 不接受、不引用真实上游；所有归档数字
  仍来自确定性 fake。守门测试钉死这一点。
- 错误分类沿用 W1 `FakeUpstream` 的命名，便于 `Router` 的 fallback 链路原样复用：
  `timeout` / `auth_error` / `upstream_5xx` / `upstream_rate_limit` / `invalid_response`；
  超时由 `timeout_s` 控制，密钥缺失直接返回 `auth_error` 而不发网络请求。
- 真实延迟与真实账单**不写进任何现有报告口径**；`simulated_latency_ms`
  字段在真实路径下含义不同，报告中必须显式标注，不与 W1 数字混用。

**R3 不变**：默认路径全部确定性 fake upstream；真实供应商只是可选适配器。
启用它需要用户自己的密钥与预算，本仓库不代填、不代跑。

## 5. 新增指标定义

| 指标 | 定义 | 边界 |
| --- | --- | --- |
| `queue_submitted` | 进入有界队列的请求数 | 不含被拒绝的提交 |
| `queue_rejected` | 队列满被拒绝数 | 由 `queue_capacity` 决定，是自变量 |
| `single_flight_calls` | 被 single-flight 选为 leader（放行进入引擎链路）的次数 | 不等于上游调用次数 |
| `single_flight_waited` | 等待 leader 完成的并发请求数 | 等待后由幂等表重放，不重复计费 |
| `workers` | 线程池大小 | 单进程内，非分布式并发 |

这些只在并发不变量场景内计数，**不是性能基准、不是 SLA、不外推生产吞吐**。

## 6. 阶段状态

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 设计写死、四能力核心、回放基准、FastAPI 薄适配、unittest、单 commit | 已交付 |
| W2（本轮） | 并发 / 有界队列 / single-flight、SSE 完整语义、可选真实供应商适配器、并发不变量测试 | **本轮交付** |
| W3 | Docker Compose 一键复现、压测脚本、trace 回放视图 | 未做 |
| W4 | 挂 P1 harness 直连跑对照（需用户 API 预算与本人执行） | 未做 |
| W5–W6 | README 打磨、简历 bullet、面试追问预案 | 未做 |

W4 仍受 `docs/design.md` §0 的口径约束：它只产真实链路可观测事件与真实成本 / 延迟，
**不产 `pass^k` 正向结论**；P1 判分器是收口红线，不改。

## 7. 本轮新增的「不许说的话」

- ❌「并发后 QPS 提升 XX%」「p95 降低 XX ms」（并发路径不测这些）
- ❌「支持分布式限流 / 跨实例缓存 / 生产级高可用」
- ❌「兼容全部 OpenAI 流式参数」
- ❌ 把真实供应商适配器的存在说成「已跑通真实模型基准」
- ❌ 把并发不变量与 W1 的 off/on 成本差相减或拼成一条曲线
- ✅「在单进程有界队列 + 线程池下，并发同幂等键只放一次上游、限流计数不超额、
    台账不丢；串行 off/on 对照与 W1 归档逐字节一致，两条路径互不混用」
