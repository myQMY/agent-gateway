# P4 设计：Agent 网关与 LLM 中间件

> 本文件在写任何实现代码之前写死，用于钉死范围、自变量、指标定义与口径边界。
> 日期：2026-11-08　仓库：`agent-gateway`（独立新仓库）　阶段：W1 设计 / W2–W3 实现 / W5–W6 文档收尾
> W2 的范围、并发与 SSE 语义、真实供应商边界见 `docs/design-w2.md`；
> W3 的可选容器复现、负载不变量与 trace 视图边界见 `docs/design-w3.md`；
> W5–W6 的叙事红线、禁用短语清单与三份交付文档的定义见 `docs/design-w5-w6.md`；
> 本文件 R1–R6 继续有效。

## 0. 定位与红线

**是什么**：一层放在 Agent 运行时与模型供应商之间的统一出口。FastAPI 只做 HTTP 适配，路由 / 缓存 / 限流 / 幂等 / 成本核算全部是纯标准库、可单测的核心逻辑。默认上游是确定性 fake，**不接真实 LLM key 也能跑完全部基准**。

**不是什么**：

- 不是 Agent 应用，不做 RAG / 向量库 / 前端 / 对话界面；
- 不是通用 Agent 评测平台，不接任意外部 Agent（与 P1 同样诚实说窄）；
- 不是生产级网关，不宣称可承载生产流量；
- 不是对 P1（`agent-reliability-harness`）的改造或续作，两者是独立仓库、独立 commit 历史。

**与 P1 的关系**：P1 是「真实 Agent 负载」的来源。候选清单 P4 原文要求「必须挂一个真实 Agent 负载（例如 P1 的运行时）跑出收益，证明不是空转的中间件」。本仓库通过两条路径兑现：

1. **回放负载（本轮已实现，零成本、可复现）**：只读 P1 的 `tasks/mvp_10.jsonl` 任务集，把每条任务的脚本动作转成请求流喂给网关；不 import P1 代码、不写 P1 目录。
2. **直连负载（W4，需用户本人执行）**：把 P1 的 `LLM_BASE_URL` 指向本网关，用新 `name` 生成新 `run_id` 跑一次 openai 后端评测。会产生真实 API 成本，本仓库不代跑、不代填密钥。

**红线（与 P1 收口决定一致）**：

| 编号 | 约束 |
| --- | --- |
| R1 | 不修改 P1 仓库任何文件；不动其判分器、`mvp_10/mvp_30.jsonl`、任何归档报告与 `run_id` |
| R2 | 不宣称「通用网关」——只实现 OpenAI 兼容 `chat/completions` 的一个子集（非流式 + SSE 流式） |
| R3 | 默认路径全部确定性 fake upstream；真实供应商只是可选适配器，不进基准 |
| R4 | 合成回放里的「延迟」是**确定性模拟值**，不得写成真实网络延迟或生产 p95 |
| R5 | 所有指标只在「同一份合成日志 + 单变量开关」内比较，不外推真实流量、不宣称成本收益可迁移 |
| R6 | 不引入 Postgres / Redis / K8s 作为硬依赖（清单要求单机一键复现）；Redis 语义缓存列为 W3 可选 |

## 1. 为什么做这个

对准上一轮审计列出的真实缺口与 2026 年在招 JD：

- 「服务化 API / REST / SSE」「分布式并发调度」「Docker/K8s 沙箱」「Redis/MQ」「成本与延迟指标」；
- 博世中国 JD 原话「使用 FastAPI 等框架将 Agent 核心能力封装为高性能、高可用的 API 服务」；
- 某后端工程 / Agent 实习生 JD 加分项含 FastAPI / Redis / Celery / 队列 / 重试限流容错 / 监控日志；
- Sixtyfour JD 原话「scheduling, retries, idempotency, rate limits, progress tracking, logs, run history」。

清单 P4 定位：后端 / 基建，5–6 周，指标「缓存命中率、p95、成本下降」，复现性「极好」，★★★★；选型行「只有 5 周、主投后端 → P4，成本最低、后端信号最强」。

## 2. 做深的四个能力

参考 LiteLLM / Portkey / Cloudflare AI Gateway 的能力边界，**选 4 个做深，不做全**。每个能力都必须有一个可测的决策点和一个可观测输出。

| 能力 | 决策点 | 可观测输出 | 明确不做 |
| --- | --- | --- | --- |
| 模型路由 + fallback | 按 `model` 与配置选上游；主上游失败时按固定顺序降级 | `route` / `fallback_used` / `attempts` | 延迟感知路由、加权负载均衡、多活 |
| 精确缓存 + LRU/TTL | 规范化请求（model + messages + temperature）算 key，命中即返回 | `cache_hit` / `cache_size` / `evictions` | 语义缓存、向量相似度、跨实例共享缓存 |
| 限流（固定窗口 RPM/TPM） | 按 key 计数，超额返回 429，不消耗上游 | `rate_limited` / `window` | 分布式限流、令牌桶平滑、配额回补 |
| 幂等键 | 同 `Idempotency-Key` 重放直接返回首次结果，不重复扣费 | `idempotent_replay` / `blocked` | 幂等窗口过期策略、跨实例锁 |

第五项「成本核算 + trace」是横切能力而非开关：每次请求记一条 span（token、单价、模拟延迟、命中链路、失败原因），供回放报告聚合。

## 3. 契约

```
GatewayEngine.handle(request) -> Decision
request : ChatRequest(model, messages, temperature, stream, idempotency_key, caller)
Decision: status(200|429|502), reply, route, cache_hit, idempotent_replay,
          rate_limited, fallback_used, attempts, cost_usd, simulated_latency_ms, reasons[]
Upstream.complete(request, attempt) -> UpstreamReply(text, in_tokens, out_tokens, ok, error_kind)
```

`FakeUpstream` 由 `SHA256(seed | 请求内容 hash | request_index | attempt | target | tag)` 派生：确定性回复、确定性 token 数、确定性失败（按 `failure_rate`）、确定性模拟延迟。不使用 `random`、不读时钟作为指标来源。

**请求内容必须参与派生**（W1 实现中修正过一次）：否则换一份负载（合成日志 vs P1 任务集）只会得到同一组成本 / 延迟数字，「挂真实负载」就退化成换个名字。修正后由 `tests/test_bridge_contract.py::test_different_load_changes_upstream_distribution` 钉死。

HTTP 层：`POST /v1/chat/completions`（OpenAI 兼容子集）、`GET /health`、`GET /metrics`、`GET /admin/config`。`stream=true` 用 SSE 分块返回。`GET /admin/config` 返回配置摘要与 hash，便于核对「同配置 → 同结果」。

## 4. 可复现性设计

沿用 P1 的纪律：**配置即自变量、产物逐字节可复现**。

- 合成日志 `synthetic_log(seed, n, repeat_rate, failure_rate)`：请求内容、顺序、重复位置全部由 SHA256 派生；同参数 → 同字节。
- 对照实验**唯一自变量是 `gateway_enabled`**：`off` 走直通 fake upstream，`on` 走网关四能力；两格共用同一份日志、同一 seed、同一价格表、同一 fake 延迟表。
- 报告附 `config_hash = sha256(json(核心配置, sort_keys))[:12]`，不含时间、机器路径、主机名。**它只锁定配置，不锁定代码**；代码版本由 git commit 锁定，两者合起来才是完整复现条件（与 P1 的 `run_id` 纪律一致）。
- 每次请求同时记 `wall_ms`（真实墙钟）与 `simulated_latency_ms`（确定性模拟）；**只有后者进报告**，前者只用于人工排查，不作为指标。
- 归档目录 `experiments/runs/<config_hash>/`，三件：`report.md` / `config.json` / `decisions.jsonl`。

## 5. 指标定义

| 指标 | 定义 | 边界 |
| --- | --- | --- |
| `cache_hit_rate` | 缓存命中数 / 总请求数 | 命中率由日志的 `repeat_rate` 决定，是**自变量**不是「优化成果」 |
| `p95_ms` | 对 `simulated_latency_ms` 排序取 95 分位 | 模拟延迟，不是网络延迟，不是生产 SLA |
| `cost_saving` | `1 - cost_on / cost_off` | 单价表写死；只反映「缓存命中不计费」这一条机制 |
| `fallback_success_rate` | 降级成功数 / 主上游失败数 | fake 失败是注入的，不代表供应商真实可用性 |
| `idempotent_blocked` | 被幂等键拦截的重复请求数 | 与缓存命中分开计数，不重复归因 |
| `rate_limited` | 被限流拒绝数 | 固定窗口，窗口边界效应如实写明 |

**必须连边界一起讲**：这些是合成回放内的相对量，用于证明「网关的四条决策链路确实在跑、且各自可测」，**不是**「上线后能省多少钱」。真实收益需要 W4 挂 P1 运行时 + 真实供应商才能测，本轮未做。

## 6. 阶段与交付边界

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 设计写死、核心四能力、回放基准、FastAPI 薄适配、unittest、单 commit | 已交付 |
| W2 | 并发 / 有界队列 / single-flight、SSE 完整语义、可选真实供应商适配器 | 已交付（见 `docs/design-w2.md`） |
| W3 | Docker Compose 一键复现、压测脚本、trace 回放视图 | 已交付（见 `docs/design-w3.md`） |
| W4 | 挂 P1 harness 直连跑对照（需用户 API 预算与本人执行） | 未做 |
| W5–W6 | 英文 README、简历 bullet、面试追问预案、叙事红线守门 | **本轮交付**（见 `docs/design-w5-w6.md`） |

W1 只交付可跑骨架，不假装做完整 5–6 周项目。W2 起按「每周 5 天、主投 Agent + 后端」推进，
每阶段一个 commit、可随时停。**W2 的并发路径与 W1 的串行 off/on 对照是两条互不相交的路径，
并发不变量不得与归档数字相减或拼曲线**（`docs/design-w2.md` §1）。后续阶段不自动扩张。

W3 新增的容器复现是**可选便利而非硬前提**：主复现路径仍是裸 Python 的全量测试与归档复验，
`docker compose` 只提供另一种一键起服务方式（`docs/design-w3.md` §1）。
压测脚本只产并发不变量，trace 视图只读归档，两者都不新增任何结论数字。

W5–W6 是**纯文档收尾，零行为代码变更**：三份对外文档（英文 README / 简历 bullet /
面试追问预案）的每一句都必须能通过 `docs/design-w5-w6.md` §4 的禁用短语扫描，
首句必须亮明「评测装置 / 运行时平台，非面向用户的 Agent 应用」。
W4 依旧未做，也不在本轮范围内：它需要用户本人执行与 API 预算，不由本仓库代跑。

## 7. 停止条件

- 用户主投方向若改为 Agent 应用 / 算法研究，本仓库暂停，不继续投入。
- 不为了「看起来更完整」引入 Redis / Postgres / K8s / 前端。
- 不为了更好看的数字调 `repeat_rate` / `failure_rate`；自变量值写进配置并随报告归档。
- 不在报告里出现「生产级」「通用」「显著提升真实成本」等措辞。

## 8. 不许说的话

- ❌「支持接入任意 Agent 并统一治理」
- ❌「成本下降 XX%」不带「合成回放 / 固定价格表」限定
- ❌「p95 = XX ms」不带「确定性模拟延迟」限定
- ❌「生产可用的 LLM 网关」「兼容全部 OpenAI 参数」
- ❌ 把 `cache_hit_rate` 说成命中率优化成果（它由日志自变量决定）
- ✅「在固定合成回放中，开关网关四能力后，缓存命中 / 限流 / 幂等 / 降级四条链路各自产生可测事件，并给出相对成本差」
