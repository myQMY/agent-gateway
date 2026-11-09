# W4 工具链设计（脚手架，不是 W4 本身）

> 日期：2026-11-09　仓库：`agent-gateway`
> 定位：**本轮只把「用户本人执行 W4」所需的启动入口、配置模板与编排脚本备好；
> 交付时 W4 未做。2026-11-09 两格真实链路回放已执行一次（用户本人、真实 API 成本），
> 但未产出收益结论。**
> `docs/design.md` 的 R1–R6、`docs/design-w2.md` 的真实供应商边界、
> `docs/design-w5-w6.md` 的叙事红线继续有效。

## 0. 为什么单独开一轮

上一轮核对 W4 执行步骤时，发现三处会让用户「照着敲也跑不通」的代码级事实，
且都不是文档措辞问题：

1. **真实上游没有服务入口**：`gateway/app.py` 末尾是 `app = create_app()`，
   从不传 `upstream`；`RealUpstreamConfig.from_env()` 里的
   `GATEWAY_REAL_UPSTREAM` 开关**全仓库除测试外没有任何调用点**。
   直接 `uvicorn gateway.app:app` 起服务，P1 只会拿到 `fake:` 开头的文本，
   钱花了却是确定性假数据。这是 W2 交付时留下的真实缺口，本轮补上。
2. **URL 路径对不上**：P1 `runtime/model.py:543` 拼的是
   `{LLM_BASE_URL}/chat/completions`，而网关只注册 `/v1/chat/completions`。
   因此 `LLM_BASE_URL` 必须是 `http://127.0.0.1:8000/v1`。
   `bridge/README.md` 原写的 `:8000` 会 404。
3. **P1 不读进程环境变量**：`eval/run.py:177` 是
   `config_from_env(load_dotenv(args.env_file))`，传进去的是文件字典，
   只认 `--env-file`。只 `export` 三个键会直接报
   「OpenAIBackend 需要 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL」。

本轮把这三处**固化成可执行的工具与机器可检的守门测试**，而不是继续写在 README 里靠人记住。

## 1. 交付物与不交付物

**交付**（`w4/` 目录，随仓库入库）：

| 文件 | 作用 |
| --- | --- |
| `w4/serve.py` | W4 专用 uvicorn 入口，显式注入 `RealUpstream`；附带一个无成本自检端点 |
| `w4/run.py` | 编排两格：默认 `--dry-run` 只打印命令，`--execute` 才真跑；自动起停网关 |
| `w4/gw_on.toml` / `w4/gw_off.toml` | 两格实验配置模板，`name` 不同、`compare` 留空 |
| `w4/on.env.example` / `w4/off.env.example` | 环境变量模板，**不含任何真实密钥** |
| `w4/README.md` | 用户只需执行的三步 |

**不交付**：

- 不填密钥、不读取密钥值、不发起任何真实 API 请求、不代跑 W4；
- 不生成任何 `experiments/runs/` 归档行，不改两组 W1 归档；
- 不修改 P1 仓库任何文件，不写 P1 目录（产物全在 `w4/out/`）；
- 不改 `gateway/app.py`、`GatewayConfig`、`GatewayEngine.handle()`、`replay.py`；
- 不把「W4 已做」写进阶段表、简历或面试文档。

## 2. 三条硬约束

### 2.1 默认 fake 不被污染（R3）

`gateway/app.py` 的模块级 `app = create_app()` **保持原样、仍走 `FakeUpstream`**。
真实上游只由 `w4/serve.py` 显式构造并注入：

```python
app = create_app(upstream=RealUpstream(RealUpstreamConfig(...)), concurrent=False)
```

`GATEWAY_REAL_UPSTREAM` 开关继续只作为 `from_env()` 的便捷方法存在，
**不接进 `create_app`**。理由：一旦让模块级 app 自动读开关，
本机任何一次 `uvicorn gateway.app:app` 都可能静默变成真实计费路径，
R3「默认路径全 fake、零 API 成本」就失守了。

`concurrent=False` 也是刻意的：W2 的并发路径与 W1 串行 off/on 归档互不相交
（`docs/design-w2.md` §1），W4 是串行评测，不把并发池混进来。

### 2.2 「拿 fake 当真实」必须被机器拦住

`w4/serve.py` 额外注册 `GET /w4/upstream`，只返回
`kind` / `base_url` / `api_key_env` / `api_key_present`（布尔，**不返回密钥值**）。
`w4/run.py --execute` 在跑 `on` 格之前先查这个端点：

- 端点不存在，或 `kind != "real"` → 说明起的是 `gateway.app:app`，立即拒绝；
- `api_key_present` 为假 → 说明网关进程没拿到密钥，立即拒绝。

这条把上一轮最贵的坑（花了钱拿到 `fake:` 文本）变成启动前零成本检查。

### 2.3 默认不花钱

`w4/run.py` **默认是 dry-run**：只打印两格将要执行的完整命令，
不启动服务、不建目录、不调用 subprocess、不产生网络请求。
只有显式 `--execute` 才进入真实路径，且执行前打印预算上界。

`--execute` 的 preflight 全部是本地文件检查 + 一次本地 HTTP GET，
**不含任何真实模型调用**。用户若想额外做一次「真模型而非 fake 文本」的
连通性探针，`w4/README.md` 给一条 curl 命令，由用户自己决定要不要花这一次调用。

## 3. 两格配置口径

| 项 | 取值 | 理由 |
| --- | --- | --- |
| `name` | `w4_gw_on` / `w4_gw_off` | 不同 `name` 才生成不同 `run_id`，否则两格互相覆盖归档（R1） |
| `tasks` | `tasks/mvp_10.jsonl` | 与 P1 既有一致，不改任务集 |
| `k` / `max_steps` / `max_json_retries` | 3 / 12 / 2 | P1 W4 首轮同值 |
| `backend` | `openai` | 真实模型路径 |
| `compare` | 空字符串 | 非空但留档不存在时 `eval/run.py:130` 会拒绝启动 |
| `[guards].enabled` | **故意不写**（全关） | 这不是 P1 的护栏 A/B；护栏开关会改变 `run_id` |
| `[seeded]` | **故意不写** | fpm=0，不注入输出级故障，避免与 W4-b/W4-c 混淆 |
| `--max-calls` | 720（可覆盖） | 单次 `eval.run` 的预算闸门，两格合计上限 1440 |
| `--report` | **不传** | 传了只写单个 md，不写 `run_dir` 三件留档证据 |

`python -m eval.w4_preflight` **不适用于本轮**：它硬编码的是 P1 内部
`w4_off_flash.toml` / `w4_all4_flash.toml`，不是 P4 的 W4 工具链。

## 4. 环境变量分工（两个进程，两套读法）

| 进程 | 读法 | 键 |
| --- | --- | --- |
| 网关（`w4.serve:app`） | `RealUpstream` 用 `os.environ.get(api_key_env)` | `LLM_API_KEY` = **真实密钥** |
| P1（`eval.run`） | `load_dotenv(--env-file)` 只读文件字典 | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` |

- `on.env`（经网关）：`LLM_BASE_URL=http://127.0.0.1:8000/v1`；
  `LLM_API_KEY` 只需**非空占位**——网关不校验 `Authorization` 头，
  但 P1 的 `build_backend` 要求三键齐全。
- `off.env`（直连）：`LLM_BASE_URL=https://api.deepseek.com`（不加 `/v1`）；
  `LLM_API_KEY` 是真实密钥，会真实计费。
- 两份 `.env` 用户从 `.example` 复制而来，`.gitignore` 忽略 `w4/*.env`，
  `off.env` 跑完建议删除。**仓库里永不出现真实密钥。**

## 5. 读数口径与不许说的话

W4（若用户执行）能拿到的只有：真实请求流下的网关决策事件
（`cache_hits` / `rate_limited` / `idempotent_blocked` / `requests`），
以及 P1 `trace` 与结果行里的真实 token 与端到端延迟。

必须连边界一起讲：

- 网关 `/metrics` 的 `total_cost_usd` **不是账单**：`price_for()` 对未知 model
  回落 `gateway-primary` 的写死单价，是台账相对量；
- `RealUpstream` 刻意不采真实延迟（`latency_ms=0.0`），避免与 W1 模拟延迟字段混用；
  真实延迟只在 P1 侧 `perf_counter` 量的端到端值里；
- 缓存命中会改变模型输出序列，走网关那格**不是一次正常评测**，实际调用数会少于 720；
- 两格自变量不同（是否经过网关 + 缓存 + 模型输出非确定性），
  **不相减、不拼曲线、不宣称省钱、不作因果解读**；
- P1 判分器是收口红线不动，`pass^k` 仍会是 0.00，
  **不产任何 `pass^k` 正向结论，不写成「护栏在真实模型上有效」**；
- 工具链交付时阶段表、简历、面试文档标「未做」；两格**已执行一次**之后，
  统一改为「已执行一次、未产收益结论」，不得写「W4 已完成」。

## 6. 停止条件与验收

- 两格已执行一次后不自动扩张；不为了补采网关 /metrics 计数再花一次 API 成本重跑。
- 验收：两条 Python 路径全量 unittest 全绿；`gateway/` 与 `experiments/`
  零改动；两组 W1 归档三件逐字节不变；`GatewayConfig` 13 字段与
  `config_hash = aa1142308d8d` 不变；P1 工作区干净；仓库内无 `sk-` 形态密钥。
- 一个 commit 收口。
