# 与 P1（agent-reliability-harness）的负载桥接

> 候选清单 P4 原文要求「必须挂一个真实 Agent 负载（例如 P1 的运行时）跑出收益，证明不是空转的中间件」。
> 本目录就是这条要求的落地说明。**本轮只做第 1 种（回放负载），第 2 种列为 W4 未做。**

## 方式 1：任务负载回放（本轮已实现，零成本、可复现）

`gateway/replay.py` 的 `load_harness_tasks` / `harness_log` **只读** P1 的 `tasks/mvp_10.jsonl`：
取 `id`、`user_utterances[0]`、`script` 的每一步，把「任务 × 步骤」展开成模型请求流，再喂给网关做 off/on 对照。

```bash
python -m bridge.p1_replay --tasks "D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl" --n 600
```

约束：

- 不 `import` P1 的任何模块，只把 `.jsonl` 当数据读；
- 不写 P1 目录，不生成任何文件到 P1；
- 默认上游是 `FakeUpstream`，**不消耗 API key、不产生真实调用**；
- 路径只有 basename（`mvp_10.jsonl`）进 `config_hash`，绝对路径不污染归档编号。

**这只是「P1 的任务负载形态」，不是 P1 运行时直连。** 报告里必须这么写，不能说成「在真实 Agent 上验证了收益」。

## 方式 2：运行时直连（W4，需用户本人执行，仍未做）

目标：把 P1 的 openai 后端指向本网关，跑一次真实模型评测，拿到真实请求流下的
网关决策事件，以及 P1 侧的真实 token 与端到端延迟。
**不产 `pass^k` 正向结论**（P1 判分器是收口红线，不改）。

### 三处曾让步骤跑不通的事实（2026-11-09 核准并修正）

1. W1 时期本文件写「需先补 W2 的真实供应商适配器」。W2 确实交付了
   `gateway/real_upstream.py`，但 **`gateway/app.py` 末尾的 `app = create_app()`
   从不注入它**，`GATEWAY_REAL_UPSTREAM` 开关在生产路径没有任何调用点。
   直接 `uvicorn gateway.app:app` 只会返回 `fake:` 开头的文本，钱花了却是假数据。
   现在由 [`w4/serve.py`](../w4/serve.py) 显式注入 `RealUpstream`，
   **不改默认 fake 路径**（R3）。
2. P1 `runtime/model.py:543` 拼的是 `{LLM_BASE_URL}/chat/completions`，
   而网关只注册 `/v1/chat/completions`。**`LLM_BASE_URL` 必须带 `/v1`**，
   原文写的 `:8000` 会 404。
3. P1 `eval/run.py:177` 是 `config_from_env(load_dotenv(--env-file))`，
   **只读 `--env-file` 指定的文件字典，不读 `export` 的环境变量**。
   网关进程才从 `os.environ` 读真实密钥 —— 两个进程分工不同，别混。

完整设计与口径见 [`docs/design-w4-tooling.md`](../docs/design-w4-tooling.md)。

### 执行步骤（脚手架已备好）

见 [`w4/README.md`](../w4/README.md)，用户只需做三件事：

1. 从 `w4/*.env.example` 复制出 `w4/on.env` / `w4/off.env`，
   **只在 `w4/off.env` 里填真实密钥**；`on.env` 的 `LLM_API_KEY=placeholder` 是刻意占位。
2. `python -m w4.run` —— 零成本预览：只打印两格命令 + preflight，
   不启动服务、不建目录、不发起任何 API 调用。
3. `export LLM_API_KEY=<真实key>` 后 `python -m w4.run --execute` ——
   自动起停 `w4.serve:app`、用 `GET /w4/upstream` 做零成本自检
   （拦住「起成 fake 上游」），依次跑 off / on 两格，产物全部落在 `w4/out/`。

两格配置 `w4/gw_on.toml` / `w4/gw_off.toml` 的 **`name` 不同才生成不同 `run_id`**，
否则两格会互相覆盖归档；`compare` 留空、护栏全关、不写 `[seeded]`，
每格 `--max-calls 720`，**不传 `--report`**（否则不写 `run_dir` 三件留档）。
不要用 `python -m eval.w4_preflight` —— 它硬编码的是 P1 内部 `w4_*_flash.toml`。

**红线**：

- 不改 P1 的判分器、`mvp_10/mvp_30.jsonl`、任何归档报告与既有 `run_id`；
- 不为了跑通而改 P1 代码；协议对不上只在本仓库改适配器；
- 真实对照会产生 API 成本，需用户明确预算并**由本人执行**；
- 网关 `total_cost_usd` 不是账单（未知 model 回落写死单价）；
  缓存会改变模型输出序列，两格**不相减、不拼曲线、不宣称省钱**；
- 本仓库不代跑、不代填密钥、仓库内不出现真实密钥；
- **W4 依旧标未做**，简历里不得写「在真实模型上验证了网关收益」。

## 为什么这样切

方式 1 让「网关不是空转」在**零成本、可复现**的前提下先可被检验：四条决策链路（限流 / 幂等 / 缓存 / 降级）各自产生可测事件，指标可归档、可重跑。方式 2 拿到的是**真实链路的可观测事件与真实 token / 延迟，不是收益证明**——两格自变量不同、缓存改变输出序列、P1 判分器不动。它需要用户自己的 API 预算与本人执行，`w4/` 脚手架已备好但**实验仍未做**。把两者分开写清楚，比含糊地说「挂了真实 Agent」扛追问。
