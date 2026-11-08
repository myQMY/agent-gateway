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

## 方式 2：运行时直连（W4，需用户本人执行，本轮未做）

目标：把 P1 的 openai 后端指向本网关，跑一次真实模型评测，量出「直连 vs 走网关」的成本 / 延迟差。

步骤（**用户自己在 P1 仓库外执行，本仓库不代跑、不代填密钥**）：

1. 起本网关：`uvicorn gateway.app:app --port 8000`（需先补 W2 的真实供应商适配器，当前默认 fake，直连只会拿到 `fake:` 文本）。
2. 在 P1 的 `.env` 里把 `LLM_BASE_URL` 指向 `http://127.0.0.1:8000`（P1 代码自拼 `/chat/completions`）。
3. 用**新的 `name` + `compare`** 跑 `python -m eval.run --config experiments/w4_all4_flash.toml --max-calls 720`；新 `name` 会生成新 `run_id`，不覆盖任何归档。
4. 比较两次运行的 token / 成本 / 延迟；`pass^k` 不要作为本实验的读数（P1 判分天花板未动）。

**红线**：

- 不改 P1 的判分器、`mvp_10/mvp_30.jsonl`、任何归档报告与既有 `run_id`；
- 不为了跑通而改 P1 代码；如果协议对不上，只在本仓库改适配器；
- 真实对照会产生 API 成本，需用户明确预算；
- 本轮不宣称做过这一步，简历里不得写「真实模型上验证了网关收益」。

## 为什么这样切

方式 1 让「网关不是空转」在**零成本、可复现**的前提下先可被检验：四条决策链路（限流 / 幂等 / 缓存 / 降级）各自产生可测事件，指标可归档、可重跑。方式 2 才是「真实收益」，但它需要 W2 的供应商适配器、用户的 API 预算与时间，属于后续阶段。把两者分开写清楚，比含糊地说「挂了真实 Agent」扛追问。
