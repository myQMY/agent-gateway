# W4 执行清单（用户本人操作）

> **本目录是脚手架，不是 W4 本身**：完整的两格对照结论依旧未做，阶段表与外部文档仍标「未做」。
> 2026-11-09 已发生过一次**部分执行**：off 格完整完成（30 trial），on 格首个请求报 502。
> **已经产生真实 API 成本**，对外不再写「零成本 / 未执行」；但两格对照结论仍未产出。
> 脚手架本身不花钱；只有 `--execute` 才会调用真实供应商并产生账单。
> 设计依据 [docs/design-w4-tooling.md](../docs/design-w4-tooling.md)。
> 真实密钥只由你本人填写，`w4/*.env` 已被 `.gitignore` 忽略，仓库里永不出现密钥。

## 先选终端语法（2026-11-09 补）

本机用 **Windows PowerShell**。`PY="..."` / `"$PY" -m ...` / `export LLM_API_KEY=...`
都是 **bash** 写法，贴进 PowerShell 会报「无法将…识别为 cmdlet」或「表达式或语句中包含
意外的标记 -m」。下面每一步都给 PowerShell 与 bash 两种写法，**只挑一种终端照抄**。

PowerShell 的两个语法点：

- 赋值是 `$PY = "路径"`（`$` 开头、等号两边可留空格），没有 `export`；
- 执行变量里的命令必须加调用运算符 `&`：`& $PY -m w4.run`。
  `"$PY" -m w4.run` 只是把路径当成字符串，`-m` 会被解析成意外标记。
  最省事的是根本不设变量，直接 `& "完整路径/python.exe" -m w4.run`。

所有步骤的工作目录恒为 `D:/文档/课题/agent/agent-gateway`。
`python -m` 只把**当前目录**放进 `sys.path`：解释器路径写得再对，
只要终端停在 `C:\Users\CYY` 之类的别处，就会报
`ModuleNotFoundError: No module named 'w4'`。新开的终端第一步先切过去：

```powershell
Set-Location "D:/文档/课题/agent/agent-gateway"
```

想少敲就把「切目录」和「执行」写在同一行（不依赖任何变量，任意目录都能跑）：

```powershell
Set-Location "D:/文档/课题/agent/agent-gateway"; & "C:/Users/CYY/.workbuddy/binaries/python/envs/agent-gateway/Scripts/python.exe" -m w4.run
```

> 下面三步里的相对路径（`w4/off.env` 等）都以此目录为基准；
> 切过去之后保持这个终端，`$PY` 变量才还在。密钥由 `w4/off.env` **单一提供**，
> 不依赖终端里的 `$env:LLM_API_KEY`（旧清单要求手设，已废弃，见「若 on 格报 502」）。

### 三条最常见的报错

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| 无法将 `PY=...` 识别为 cmdlet / 意外的标记 `-m` | 贴了 bash 语法 | 用 PowerShell 写法，执行变量里的命令前加 `&` |
| `ModuleNotFoundError: No module named 'w4'` | 当前目录不是仓库根 | 先 `Set-Location` 到仓库根，或用上面的一行版 |
| `preflight 未通过，已中止` | `w4/*.env` 没复制，或 key 仍是占位符 | 回到第 1 步；此时零成本、未发起任何 API 调用 |

## 三步

### 1. 填密钥（唯一必须手工做的事）

PowerShell：

```powershell
Copy-Item w4/on.env.example  w4/on.env
Copy-Item w4/off.env.example w4/off.env
```

bash：

```bash
cp w4/on.env.example  w4/on.env
cp w4/off.env.example w4/off.env
```

编辑 `w4/off.env`，把 `REPLACE_ME_WITH_REAL_KEY` 换成你自己的 DeepSeek API key。
`w4/on.env` 不用改——`LLM_API_KEY=placeholder` 是刻意占位：网关不校验
`Authorization` 头，但 P1 的 `build_backend` 要求三键齐全。

### 2. 预览（零成本，建议先跑一次）

PowerShell（两条一起贴，或只用第一行 + 第三行的完整路径写法）：

```powershell
$PY = "C:/Users/CYY/.workbuddy/binaries/python/envs/agent-gateway/Scripts/python.exe"
& $PY -m w4.run
```

不设变量的最简写法：

```powershell
& "C:/Users/CYY/.workbuddy/binaries/python/envs/agent-gateway/Scripts/python.exe" -m w4.run
```

bash：

```bash
PY="C:/Users/CYY/.workbuddy/binaries/python/envs/agent-gateway/Scripts/python.exe"
"$PY" -m w4.run
```

只打印两格完整命令与 preflight 结果，**不启动服务、不建目录、不发起任何 API 调用**。
preflight 全部通过再进第 3 步。`$PY` 必须是装了 `requirements.txt` 的 Python
（需要 `uvicorn` / `fastapi`）；裸标准库 Python 只能跑 dry-run。

### 3. 执行（会产生真实成本）

PowerShell：

```powershell
& $PY -m w4.run --execute
```

bash：

```bash
"$PY" -m w4.run --execute
```

**不用再手设 `$env:LLM_API_KEY` / `export`**：`w4/run.py` 会从 `w4/off.env`
读取同一个 key，只覆盖网关子进程的环境变量（不打印、不落盘、不写进产物）。
密钥只有一个真相源，避免「终端里那份和文件里那份不一致」导致上游 401、
网关却映射成 502。手动设置仍兼容，但会被 `off.env` 覆盖。

脚本自动完成：起 `w4.serve:app` → 用 `GET /w4/upstream` 做零成本自检
（确认不是 `fake` 上游、密钥确实注入）→ 依次跑 `off` / `on` 两格 → 关闭网关。

两格各 `--max-calls 720`，合计调用上限 1440 次。`experiments/w4_budget_estimate.md`
给出的离线上界约 0.2484 USD/格（≈¥1.8），最保守按 ¥8.55 准备，**以服务商账单为准**。

### 若 on 格报 `HTTP Error 502`

502 是 `GatewayEngine` 把「上游全部失败」映射出来的状态码，**不代表 P1 或判分器
出错**；P1 的 traceback 只显示 `HTTP Error 502`，看不到网关响应体里的 `error.message`。
现在 `w4/serve.py` 会把每次上游失败的 `error_kind` 记在内存里，`w4/run.py` 在 cell
失败时自动通过 `GET /w4/last-error` 打印出来（零成本，不含密钥与消息内容）：

```
  - deepseek-flash: auth_error (attempt 0)
  - gateway-fallback: upstream_5xx (attempt 1)
```

| error_kind | 含义 | 处理 |
| --- | --- | --- |
| `auth_error` | 上游 401/403，或网关进程没读到 key | 核对 `w4/off.env` 的 key 是否有效 |
| `upstream_rate_limit` | 上游 429 | 稍后重试，或调小 `--max-calls` |
| `upstream_5xx` | 上游返回其它 HTTP 错误（含 400） | 看服务商状态；`gateway-fallback` 是内部假名，第二次必然失败，属噪音 |
| `timeout` | 30s 超时或网络不可达 | 检查网络 / 代理后重试 |
| `invalid_response` | 上游响应解析失败 | 核对模型名与服务商协议 |

**实测排错（2026-11-09 首次执行）**：那次 502 的根因是旧清单要求把 key 填两处
（`w4/off.env` 与终端 `$env:LLM_API_KEY`），两处不一致 → 上游 401 → `GatewayEngine`
统一映射成 502。零成本确认（只输出 True/False，不打印密钥）：

```powershell
$line = Get-Content w4/off.env | Where-Object { $_ -match '^\s*LLM_API_KEY\s*=' } | Select-Object -First 1
($line -split '=',2)[1].Trim() -eq $env:LLM_API_KEY
```

`False` 即坐实该根因。`w4/run.py` 现在**强制只从 `w4/off.env` 取 key** 注入网关子进程
（守门测试覆盖「终端里另有一份值时仍以文件为准」），所以重跑前把旧变量清掉即可，
不必再手设：

```powershell
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
```

> ⚠️ **不要改 `w4/on.env`**：它的 `LLM_API_KEY=placeholder` 是刻意占位，与
> `w4/off.env` 里的真 key **不同属于设计如此**，不是故障。网关不校验客户端
> `Authorization` 头，`RealUpstream` 只读自己进程的 `os.environ["LLM_API_KEY"]`；
> 把真 key 填进 on.env 只会多一个泄漏面。`/w4/upstream` 的 `api_key_present`
> 只验「非空」，验不出两份值是否一致。

**只重跑失败的那一格，别重跑已成功的一格**（off 的产物保留在 `w4/out/runs-off/`）：

```powershell
& $PY -m w4.run --cell on --execute
```

只想跑一格做调试：`--cell off`（直连，不需要网关）或 `--cell on`（经网关）。
想额外花一次调用确认返回的不是 `fake:` 文本：

PowerShell（第二个终端用 `Invoke-RestMethod`；PowerShell 5.1 里 `curl` 是
`Invoke-WebRequest` 的别名，参数完全不同，要用得写 `curl.exe`）：

```powershell
& $PY -m w4.run --serve-only
# 另开终端（这一句会产生一次真实调用）
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/chat/completions -Method Post -ContentType "application/json" -Body '{"model":"deepseek-flash","messages":[{"role":"user","content":"reply OK"}]}'
```

bash：

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

> 注意：`w4/on.env` / `w4/off.env` 是你本地的工作文件，被 `.gitignore` 排除。
> 按本清单填好之后，全量测试应当仍然全绿；若某条守门测试因这两个文件存在而失败，
> 那是测试把「合法本地文件」误判成「已入库密钥」，属于脚手架缺陷。

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
- 仓库、简历、面试文档里 **W4 依旧标「未做」**，直到完整两格都跑完；
  已发生的部分执行与真实成本如实讲，不写成「已验证网关收益」。
