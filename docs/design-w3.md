# W3 设计补充：可选容器复现 / 负载不变量 / trace 回放视图

> 本文件在写任何 W3 实现代码之前写死。`docs/design.md` 的 R1–R6 红线全部继续有效，
> `docs/design-w2.md` 的「两条互不相交路径」继续有效；本文件只增补 W3 的范围与边界。
> 日期：2026-11-08　仓库：`agent-gateway`　阶段：W3
> `docs/design-w2.md` 的阶段表记录的是 **W2 时点**状态，按纪律不回溯改字；阶段总表以 `docs/design.md` 为准。

## 0. 本轮补什么

按 `docs/design.md` §6 的原始定义，W3 只补三块：**Docker Compose 一键复现**、
**压测脚本**、**trace 回放视图**。挂 P1 运行时直连仍属 W4（需用户本人执行 + API 预算），
README / 简历 bullet 仍属 W5–W6。

继续不补：分布式限流、跨实例缓存、语义缓存、Redis / Postgres / MQ / K8s、前端框架、
实时 trace 服务、性能基准数字、任何新的第三方 Python 包（R6 / R3 / R4 / R5）。

## 1. 最重要的取舍：Docker 是可选便利，不是复现前提

R6 要求「单机一键复现」且不引入 Redis / K8s 作为硬依赖。若把 Docker 写成
「必须先装 Docker 才能复现」，就直接违反 R6，也把 P1 那条「clone 下来零依赖跑全绿」
的差异化弄丢了。因此：

| 路径 | 命令 | 需要什么 | 地位 |
| --- | --- | --- | --- |
| **主复现路径** | `python -m unittest discover -s tests` + `python -m gateway.replay` | 只有 Python 标准库 | 唯一权威路径；归档逐字节校验走这里 |
| 可选 HTTP 服务 | `uvicorn gateway.app:app` | + fastapi / uvicorn / httpx | W1 起就有 |
| 可选容器 | `docker compose up --build gateway` | + Docker | **本轮新增，纯便利** |

硬约束：

- 容器镜像只装 `requirements.txt` 里已有的 3 个包，不新增任何依赖、不装数据库、
  不装 `curl`、不跑多副本、不写 K8s manifest。
- `verify` 服务跑的是**与主路径完全相同**的离线命令（unittest + bench + loadgen + trace 校验），
  它不依赖 `gateway` 服务、不访问网络、不产生 API 成本。
- 无 Docker 的机器上，W3 三块能力全部可用：`loadgen` 与 `trace` 都是纯标准库 CLI。
- 若本机没有 Docker，本轮**不得宣称容器已实跑验证**，只能交付静态清单与守门测试。
- 不写「容器化部署 / 生产可用 / K8s 就绪」。容器只是「换一种方式把同一份代码跑起来」。

**实施注记**：本机 `docker --version` 不存在，因此本轮**未实跑** `docker build` /
`docker compose up`。交付的是静态清单与守门测试（文件存在、YAML 可解析、
无禁用服务、`verify` 命令与主路径一致）。容器能否构建需在装有 Docker 的机器上
自行验证，本轮如实标注未验证。

## 2. 压测脚本的定位：负载生成器，不是性能基准

W2 的 `gateway/bench.py` 是固定 4 项自检。W3 的 `gateway/loadgen.py` 把它推广成
**可配参数的并发负载生成器**，但口径完全不变：只产与线程调度无关的不变量。

三个场景（全部可复现，全部可断言）：

| 场景 | 做法 | 确定性不变量 |
| --- | --- | --- |
| `single_flight` | N 个并发请求带同一个 `idempotency_key` | 上游恰好调用 1 次；决策数 = N；台账 span = N |
| `rate_limit` | N 个并发请求，`rpm_limit=L`、`failure_rate=0` | 200 恰好 L 个、429 恰好 N−L 个；台账 span = N |
| `capacity_gate` | **不提交线程**，只对有界闸门连续 `try_put` C+M 次 | 成功恰好 C 次、拒绝恰好 M 次；逐次释放后可再成功 C 次 |

`capacity_gate` 刻意绕开线程池：并发下「队列满」的拒绝数依赖线程调度时序，
不是不变量。把它单独做成纯闸门测试，才能既验证背压、又保持确定性。
并发场景里的 `queue_rejected` 与 `single_flight_waited` 只作为**观测值**输出，
标注 `scheduling-dependent`，**从不作为断言**。

**实施注记（W3 实测修正的一处真实缺陷）**：初版 `single_flight` 场景直接提交 N 个
同键请求，fake 上游没有延迟，leader 在其它请求到达前就完成，
`single_flight_waited` 恒为 0 —— 并发等待分支一次都没触发，所谓「并发自检」
退化成串行回放。改为 `BlockingUpstream`：首个上游调用阻塞到 `submit_many`
全部提交完成再放行，实测 `waited > 0` 且上游仍恰好 1 次。阻塞用
`threading.Event`（带超时防死锁），不读时钟作为指标。

**口径诚实性**：同键场景下「上游恰好 1 次」是**幂等表 + 精确缓存**共同保证的结果，
不是 single-flight 单独功劳；waiter 等 leader 完成后重走 `engine.handle`，
由幂等表重放。文档与输出都不把它归因为某一条机制。

**输出**：单个 JSON（默认 stdout，`--json-out` 可选写文件），字段只有配置、
不变量与 `checks[].pass`。**禁止出现**：QPS / RPS / 吞吐 / p95 / P99 / 墙钟耗时 /
elapsed / `perf_counter` / 机器名 / 主机名 / 真实延迟。源码不 `import time`。
`--json-out` 不得指向 `experiments/runs/`；建议写 `results/`（已被 `.gitignore` 排除）。

**不写归档行**：loadgen 不调用 `write_archive`、不改 `GatewayConfig` 任何字段、
不碰 `config_hash`、不产出 `cache_hit_rate` / `cost_saving` / `p95_ms`，
也不与 W1 的 off/on 数字相减或拼曲线（W2 §1 的两条路径继续成立）。

## 3. trace 回放视图：只读归档的确定性渲染

`gateway/trace.py` 读 `experiments/runs/<run_id>/` 里的 `config.json` 与
`decisions.jsonl`，生成一份**自包含静态 HTML**（内联 CSS、零 JS、零外链、零 CDN、
零字体请求），纯标准库 `html.escape` 转义。

页面内容限于「把归档里已有的数字与链路画出来」：

1. 头部：`run_id` / `config_hash` / 负载来源 / 边界声明（fake 上游、模拟延迟、不外推）；
2. 配置摘要表（逐字段来自 `config.json`）；
3. `off` / `on` 两格汇总（请求数、200/429/502、缓存命中、幂等重放、限流、降级、
   总成本、`p95_ms` **必须带「模拟」标签**）；
4. 事件分布条（纯 CSS 百分比，由归档行计数派生）；
5. 决策链路时间线：按 `index` 顺序展示每格前 `--rows` 条，标出
   `status` / `route` / `cache_hit` / `idempotent_replay` / `rate_limited` /
   `fallback_used` / `attempts` / `cost_usd` / `simulated_latency_ms` / `reasons`；
6. 页脚：只读声明与复现命令。

**硬约束**：

- 汇总统计基于**全量**归档行；时间线只展示前 `--rows` 条，页面必须显式写出
  「统计基于全量 N 条，时间线展示前 R 条」，`--rows 0` 表示全量。
- 输出不含时钟、随机、机器路径、绝对路径、主机名；同归档 → HTML 逐字节相同。
  `python -m gateway.trace --run <id> --check` 重渲染并与已提交视图比对，作为可复验证据。
- **只读**：不修改 `report.md` / `config.json` / `decisions.jsonl` 任何一个字节，
  不生成新指标，不新增结论，不写 `experiments/runs/` 目录内任何文件。
- 视图是「归档的可视化」，**不是新实验**；不宣称实时 trace、不宣称可交互筛选、
  不提供 Web 服务或数据库。
- 提交一份由 W1 归档 `replay-aa1142308d8d` 生成的示例视图
  `experiments/views/<run_id>.html` 作为证据，它本身可逐字节复验。

## 4. 新增文件

| 文件 | 作用 |
| --- | --- |
| `docs/design-w3.md` | 本文件 |
| `Dockerfile` / `docker-compose.yml` / `.dockerignore` | 可选容器复现（非硬依赖） |
| `gateway/loadgen.py` | 可配参数负载生成器 + 并发不变量自检 |
| `gateway/trace.py` | 只读归档 → 确定性静态 HTML |
| `experiments/views/replay-aa1142308d8d.html` | 示例视图（确定性、可复验） |
| `tests/test_w3.py` | 行为测试 |
| `tests/test_project_contract.py` | 追加 W3 守门断言 |

`GatewayConfig` / `GatewayEngine.handle()` / `replay.py` / `sse.py` /
`concurrency.py` / `real_upstream.py` **一行不改**；W1 两组归档三件逐字节不变。

## 5. 本轮新增的字段定义

| 字段 | 定义 | 边界 |
| --- | --- | --- |
| `upstream_calls` | `CountingUpstream` 记录的实际 `complete` 次数 | 只用于 single-flight 断言，不是吞吐 |
| `queue_rejected` | 有界闸门拒绝的提交数 | **调度相关，输出但不断言** |
| `capacity_gate_admitted` / `_rejected` | 纯闸门（无线程）的接受 / 拒绝数 | 由 `queue_capacity` 决定，是自变量 |
| `timeline_rows` | 时间线展示条数 | 只是展示上限，不影响全量统计 |

## 6. 阶段状态

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 四能力核心、串行 off/on 归档、FastAPI 薄适配 | 已交付 |
| W2 | 并发 / 有界闸门 / single-flight、SSE 完整语义、可选真实供应商适配器 | 已交付 |
| W3（本轮） | 可选容器复现、负载不变量、trace 回放视图 | **本轮交付** |
| W4 | 挂 P1 harness 直连跑对照（需用户 API 预算与本人执行） | 未做 |
| W5–W6 | README 打磨、简历 bullet、面试追问预案 | 未做 |

W4 的口径不变：只产真实链路可观测事件与真实成本 / 延迟，**不产 `pass^k` 正向结论**；
P1 判分器是收口红线，不改。

## 7. 本轮新增的「不许说的话」

- ❌「已容器化部署 / K8s 就绪 / 生产可用」
- ❌「压测 QPS = XX」「p95 降低 XX ms」「吞吐提升 XX%」（loadgen 不测这些）
- ❌ 把 `queue_rejected` 的并发观测值说成可复现结论
- ❌ 把 trace 视图说成「实时链路追踪 / 可观测平台」
- ❌ 把视图里的 `p95_ms` 说成网络延迟
- ❌「必须先装 Docker 才能复现本项目」
- ✅「在裸 Python 下全量测试与归档复验即可完成复现；容器只是可选的一键起服务方式，
    负载生成器只验证并发不变量，trace 视图只是把已有归档确定性画出来」
