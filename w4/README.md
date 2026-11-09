# W4 执行清单（用户本人操作）

> **本目录是脚手架，不是 W4 本身**：W4 依旧未做、未执行、未产生任何 API 成本。
> 设计依据 [docs/design-w4-tooling.md](../docs/design-w4-tooling.md)。
> 真实密钥只由你本人填写，`w4/*.env` 已被 `.gitignore` 忽略，仓库里永不出现密钥。

## 三步

### 1. 填密钥（唯一必须手工做的事）

在 `D:/文档/课题/agent/agent-gateway` 根目录：

```bash
cp w4/on.env.example  w4/on.env
cp w4/off.env.example w4/off.env
```

编辑 `w4/off.env`，把 `REPLACE_ME_WITH_REAL_KEY` 换成你自己的 DeepSeek API key。
`w4/on.env` 不用改——`LLM_API_KEY=placeholder` 是刻意占位：网关不校验
`Authorization` 头，但 P1 的 `build_backend` 要求三键齐全。

### 2. 预览（零成本，建议先跑一次）

```bash
PY="C:/Users/CYY/.workbuddy/binaries/python/envs/agent-gateway/Scripts/python.exe"
"$PY" -m w4.run
```

只打印两格完整命令与 preflight 结果，**不启动服务、不建目录、不发起任何 API 调用**。
preflight 全部通过再进第 3 步。`$PY` 必须是装了 `requirements.txt` 的 Python
（需要 `uvicorn` / `fastapi`）；裸标准库 Python 只能跑 dry-run。

### 3. 执行（会产生真实成本）

```bash
export LLM_API_KEY="你的真实key"   # 网关进程读的是这个，不是 env 文件
"$PY" -m w4.run --execute
```

脚本自动完成：起 `w4.serve:app` → 用 `GET /w4/upstream` 做零成本自检
（确认不是 `fake` 上游、密钥确实注入）→ 依次跑 `off` / `on` 两格 → 关闭网关。

两格各 `--max-calls 720`，合计调用上限 1440 次。`experiments/w4_budget_estimate.md`
给出的离线上界约 0.2484 USD/格（≈¥1.8），最保守按 ¥8.55 准备，**以服务商账单为准**。

只想跑一格做调试：`--cell off`（直连，不需要网关）或 `--cell on`（经网关）。
想额外花一次调用确认返回的不是 `fake:` 文本：

```bash
"$PY" -m w4.run --serve-only
# 另开终端（这一句会产生一次真实调用）
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"只回复 OK"}]}'
```

## 产物与验收

两格产物全部落在 `w4/out/`（不入库），**P1 仓库零改动**：

- `w4/out/runs-off/<run_id>/` 与 `w4/out/runs-on/<run_id>/`，各有
  `config.json` / `result_rows.jsonl` / `report.md`；两个 `run_id` 必须不同。
- `w4/out/results-*/`、`w4/out/traces-*/`：P1 的结果行与全链路 trace。
- 网关 `/metrics`：真实请求流下的 `requests` / `cache_hits` / `rate_limited` /
  `idempotent_blocked` 等决策事件。
- 跑完检查 `git -C "D:/文档/课题/agent/项目1" status --short` 为空，并删除 `w4/off.env`。

## 必须连边界一起讲

- 网关 `/metrics` 的 `total_cost_usd` **不是账单**：`price_for()` 对未知 model
  回落 `gateway-primary` 的写死单价，只是台账相对量。
- `RealUpstream` 刻意不采真实延迟，避免与 W1 模拟延迟字段混用；真实端到端延迟
  只在 P1 的 `trace` / 结果行里（由 `perf_counter` 量得）。
- 缓存命中会改变模型输出序列，经网关那格**不是一次正常评测**，实际调用数会少于 720。
- 两格自变量不同（是否经网关 + 缓存 + 模型输出非确定性）：
  **不相减、不拼曲线、不宣称省钱、不作因果解读**。
- P1 判分器是收口红线不动，`pass^k` 仍会是 0.00；这一格**不产任何 `pass^k`
  正向结论**，也不写成「护栏在真实模型上有效」。

## 红线

- 不改 P1 判分器、`mvp_10/mvp_30.jsonl`、任何归档与既有 `run_id`；
- 不为了跑通而改 P1 代码，协议对不上只改本仓库适配器；
- 不用 `python -m eval.w4_preflight`——它硬编码的是 P1 内部 `w4_*_flash.toml`；
- 仓库、简历、面试文档里 **W4 依旧标「未做」**，直到你本人真正执行完。
