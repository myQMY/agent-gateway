# W5–W6 设计补充：英文 README / 简历 bullet / 面试追问预案

> 本文件在写任何 W5–W6 交付文档之前写死。`docs/design.md` 的 R1–R6 红线全部继续有效，
> `docs/design-w2.md` 的「两条互不相交路径」、`docs/design-w3.md` 的「容器可选 / 负载不变量 /
> trace 只读」全部继续有效；本文件只增补叙事层的范围与边界。
> 日期：2026-11-09　仓库：`agent-gateway`　阶段：W5–W6
> 阶段总表以 `docs/design.md` 为准；`design-w2.md` / `design-w3.md` 的阶段表记录各自时点状态，按纪律不回溯改字。

## 0. 本轮补什么、不补什么

按 `docs/design.md` §6 的原始定义，W5–W6 只补三块纯文档交付：

| 文件 | 作用 | 读者 |
| --- | --- | --- |
| `README.en.md` | 与中文 README 结构对齐的英文版 | 面试官 / 海外岗 / 英文简历链接 |
| `docs/resume.md` | 可直接抄进简历的 bullet + 口径红线 | 用户本人 |
| `docs/interview.md` | 分组追问预案（可说的话 / 依据 / 不许越界的话） | 用户本人 |

**W4 仍不做**，本轮不碰：挂 P1 运行时直连需用户本人执行 + API 预算，且 P1 判分器是收口红线。
W5–W6 不得把 W4 写成已做、也不得替用户预设「跑完就能得到正向结论」。

**明确不补**（`design.md` §7 停止条件继续有效）：

- 不改 `gateway/` 任何一行代码、不加 `GatewayConfig` 字段、不动 `config_hash`、不动两组 W1 归档；
- 不开新实验组、不新增任何指标或结论数字、不写 `experiments/runs/`；
- 不为了让简历好看引入 Redis / Postgres / K8s / 前端 / 真实供应商基准；
- 不做英文版以外的第四份对外文档，不写 README 中文版没有的承诺；
- 不把两个项目包装成「面向用户的 Agent 应用」或「通用 Agent 治理平台」。

本轮**零行为代码变更**，因此验证方式从行为测试改为**守门测试 + 全量回归 + 归档逐字节复验**：
`tests/test_w5_w6.py` 钉死叙事红线，`test_project_contract.py` 追加索引断言，
两条 Python 路径跑全量测试（W5–W6 交付时 157 条，本轮 248 条），两组 W1 归档三件仍须逐字节不变。

## 1. 最重要的一条：首句亮明定位

P1 的收口红线要求「简历首句须亮明『评测装置 / 运行时平台，非面向用户的 Agent 应用』」。
本轮把它落实为**机器可检的硬约束**：

- `docs/resume.md` 的**首个正文段落**必须同时出现「评测装置」「运行时平台」「非面向用户的 Agent 应用」三个要素；
- `README.en.md` 的**首个正文段落**必须给出等价英文表述（`evaluation harness / runtime platform, not a user-facing Agent application`）；
- 守门测试取「标题之后、第一个空行之前的连续非空行块」，去掉 Markdown 引用符 `>`
  与加粗符 `*`、把换行折成空格后做子串断言，不靠全文 grep —— 防止把定位藏在文末充数。

理由：这两个项目最容易被误读成「做了一个客服 Agent」或「做了个能评测所有 Agent 的平台」。
一旦面试官按错误定位提问，后面所有解释都在逆风。首句纠偏是成本最低、收益最高的一句。

## 2. 叙事主线：两个项目是什么关系

主投方向已确认为 **A 类：Agent 基建 / AI 后端 / 评测平台**（每周 5 天）。
简历与面试必须给出一条连贯主线，而不是两个孤立项目：

```
P1 agent-reliability-harness          P4 agent-gateway
「运行时 + 评测可观测」        →      「运行时与供应商之间的服务化出口」
   被测对象是 harness 本身              被测对象是网关的四条决策链路
   pass^k / trace / 单变量对照          缓存 / 限流 / 幂等 / 路由 + 并发不变量
        共同方法论：配置即自变量、单变量对照、产物逐字节可复现、边界写进文档
```

- **共同能力信号**：可复现实验设计、可观测性、单变量纪律、确定性归档、诚实标注缺口；
- **互补而非续作**：P4 是独立仓库、独立 commit 历史，只**只读** P1 的 `mvp_10.jsonl` 做回放负载；
  `docs/resume.md` 必须写明这一点，不得说成「在 P1 基础上迭代」或「P1 的第二个版本」；
- **不投算法研究 / RL 后训练**（项目零训练），缺口靠讲清取舍 + 面试前补概念，不靠改项目补。

## 3. 数字口径：每个数字必须带边界

`docs/resume.md` 与 `docs/interview.md` 引用的数字，一律从已归档报告取，
且**必须与边界写在同一段**，不允许只抄漂亮数字：

| 数字 | 出处 | 必须同段出现的边界 |
| --- | --- | --- |
| `612 unittest 全绿` / `23 commits` / `22 份报告 + 14 组归档` | P1 README / 归档 | 相当比例是读归档与源码的守门测试；对外说「605 全绿」须带此限定 |
| `off 0.1433 → all4 0.3000（+0.1567）`、`fpm=0 仅 0.2000（+0.0567）`、`缺口 0.1000 / 36.2%` | P1 `w3_attribution` | fpm 只控四类输出级故障、非推理能力；`fpm=0` 不是天花板；0.10 来自 idempotency 修 T03 固有重复写；**2×8 全因子 + 30 seed 描述性统计，不是 A/B、不作因果解读、不外推真实模型** |
| `W4 三轮两格 pass^k 均为 0.00` | P1 `w4*report` | 判分器自由文本精确匹配是根因；H1 未被检验（分母 0）不是被证伪；**不是护栏在真实模型上有效** |
| `pass^k 分辨率恒 1/N、与 k 无关`；`mvp_30 步进 0.10→0.0333、信息位点 3→19` | P1 `w3_resolution` | 跨任务集只谈分辨率、scripted 基线不可相减；W3/W4 自变量不同，数字不拼曲线 |
| `成本差 0.1185 / 0.1822` | P4 两组 W1 归档 | 只在该合成回放 + 固定价格表内成立；全 fake 上游、零 API 成本、命中率由 `repeat_rate` 决定；**不外推生产、不宣称省钱** |
| `248 unittest` / `10 commits` / `61 tracked` | P4 仓库 | 相当比例是读归档与源码的守门测试；14 个 HTTP 测试在裸标准库下 skip，隔离 venv 全跑 |
| `32 同 key → 上游恰 1 次`、`40 @ rpm_limit=3 → 200 恰 3 / 429 恰 37` | `gateway/bench.py` / `loadgen.py` | 并发不变量，不是 QPS / SLA / 生产吞吐；那 1 次由幂等表 + 精确缓存共同保证 |

守门测试用「关键数字 + 同段边界词」的方式抽查若干条，防止出现孤立数字。
抽查**跳过以 `#` 起始的标题段落**：`docs/interview.md` 的「### C1.「0.1433 → 0.3000，
是不是说明……？」」是提问句而非结论断言，边界写在紧随其后的回答段落里；
把提问标题也算进去会误报，但回答段落漏写边界仍会被拦住。

## 4. 不许说的话（机器可读清单）

下列短语**不得出现在三份对外文档的正文**（反面示例区除外，见 §5）。
清单由 HTML 注释对 `BANNED-PHRASES-START` / `BANNED-PHRASES-END` 包住，
以 fenced code block 逐行给出，供 `tests/test_w5_w6.py` 直接解析（单一真相来源，
避免测试里再抄一份、也避免中文引号歧义）。中英文清单合并扫描所有三份文档：

<!-- BANNED-PHRASES-START -->
```
智能客服
通用 Agent 评测平台
支持接入任意 Agent
护栏显著提升真实模型通过率
护栏在真实模型上有效
W3 被完整验证
W3 与 W4 数字拼成曲线
上线后能省钱
生产级网关
生产可用的 LLM 网关
真实 p95
压测 QPS
已容器化部署
K8s 就绪
Docker 已实跑
W4 已完成
W4 产出了正向 pass^k 结论
W4 验证了网关收益
在真实模型上验证了网关收益
真实链路证明省钱
显著提升真实成本
兼容全部 OpenAI 参数
把命中率说成优化成果
production-ready gateway
general-purpose agent evaluation platform
proven effective on real models
reduced production cost by
real p95 latency
benchmarked QPS
deployed with Kubernetes
W4 completed
W4 produced a positive pass^k result
verified gateway benefit on real models
W4 proved cost savings
positive pass^k from W4
```
<!-- BANNED-PHRASES-END -->

**阶段状态类短语（STRICT，不享否定豁免）**。2026-11-09 W4 口径由「未做」改为「两格
已执行一次、未产收益结论」后，`not done` / `W4 未做` 这类**否定句本身就是过时口径**，
不能因为行内含 `not` / `未` 就被 §5 第 6 条豁免放过。因此另设清单，命中即判越界：

<!-- STRICT-PHRASES-START -->
```
W4 未做
W4 没做
W4 not done
W4 is not done
W4 has not been done
真实运行时直连未做
not done
```
<!-- STRICT-PHRASES-END -->

历史陈述（「交付时 W4 未做」「W5–W6 那轮 W4 未做」）只出现在设计文档里，按 §5
第 4 条设计文档不参与扫描，不受影响；**三份对外文档一律不得再用**。

## 5. 反面示例的隔离约定（守门测试怎么扫）

`docs/resume.md` 与 `docs/interview.md` **必须**列出「不能写进简历的话 / 不许越界的话」——
这是用户要的教学价值，不能因为守门测试就删掉。为避免 W3 踩过的
「注释里写了禁词、守门测试误伤自己」问题，本轮统一约定：

1. 所有反面示例集中放在 HTML 注释对
   `<!-- FORBIDDEN-EXAMPLES-START -->` 与 `<!-- FORBIDDEN-EXAMPLES-END -->` 之间；
2. 或单独成行且以 `❌` 开头（`- ❌` 这类列表项同样按 `❌` 起始处理）；
3. 守门测试**先剥离**这两类内容，再对剩余正文扫描 §4 的禁用短语；
4. `docs/design-w5-w6.md`（本文件）是红线定义处，含完整禁用清单，**不参与扫描**；
5. 剥离开头/结尾标记本身必须存在，否则守门测试失败 —— 防止后来有人悄悄去掉隔离区；
6. **否定语境豁免**：文档正文需要写「不是 X」「不得宣称 X」这类边界声明，
   而 `BANNED-PHRASES` 里同样是这些词。因此扫描按行进行：若该行同时含
   `不是 / 并非 / 不得 / 不能 / 不宣称 / 不写 / 不说 / 绝不 / 未 / 非 / 无 / 不产 / 不外推 /
   not / never / no / cannot / does not / is not / are not` 之一，则该行跳过。
   这保证「不得说 X」能写，而「X」作为正面断言仍被拦住。
   豁免只作用于单行，跨行的正面断言依然会被扫到。
   **例外**：§4 的 `STRICT-PHRASES` 清单不受本条豁免约束，命中即判越界 ——
   这类短语描述的是阶段状态，否定句式本身仍是过时口径。
7. 清单里的短语一律**小写比较**（英文），中文按原样子串比较；标点与 Markdown
   强调符不影响匹配，因为扫描前只做必要的空白归一化，不做词形还原。

## 6. 三份文档的结构契约

**`README.en.md`**（与中文 README 章节一一对应，不新增承诺）：

- 首段定位 + 30-second overview + What it is / Two disjoint paths / How to run /
  Key results / Boundaries / Evidence index / Stages，章节数与中文版对齐；
- 术语固定：`deterministic fake upstream`、`simulated latency`、`concurrency invariants`、
  `serial off/on baseline`、`byte-for-byte reproducible`、`optional container replay`、
  `read-only trace view`、`W4 Executed once, no benefit claim`；
- 明确写出 Docker 未实跑、真实供应商默认关闭、W4 两格已执行一次但未产收益结论，
  与中文版口径逐条对应；**W4 口径**由此处的禁用短语清单与守门测试共同钉死。

**`docs/resume.md`**：

1. 首段定位（§1 硬约束）；
2. 一句话主线（§2）；
3. 项目一 P1：4 条 bullet，每条带可核验数字与边界；
4. 项目二 P4：4 条 bullet，同上；
5. 工程方法与缺陷各一条（两个实测真缺陷：faulthandler 定位线程池退出挂死、
   `BlockingUpstream` 打开并发窗口）；
6. 岗位匹配与缺口（A 类 / 不投算法研究）；
7. 可核验数字表 + 反面示例区（§5 约定）。

**`docs/interview.md`**：按定位 / 架构 / 数字 / 边界 / 工程缺陷 / 缺口 / 出勤方向七组，
每题三段式：**可直接说的话**（引用块）、**依据文件**、**不许越界的话**（`❌` 行，落在反面示例区或 `❌` 前缀行）。

## 7. 索引与阶段表更新

- `docs/design.md`：阶段表 W5–W6 改「本轮交付」并指向本文件；头部阶段行加上 W5–W6；
- `README.md`：头部加英文版链接、证据索引加三份新文档、阶段表 W5–W6 改「本轮交付」；
- 守门测试断言这两处索引存在，防止文档写完却没人能从首屏找到。

## 8. 新增文件与变更范围

| 文件 | 变更 |
| --- | --- |
| `docs/design-w5-w6.md` | 本文件（新增） |
| `README.en.md` | 新增 |
| `docs/resume.md` | 新增 |
| `docs/interview.md` | 新增 |
| `tests/test_w5_w6.py` | 新增，守门测试（禁用短语扫描 / 扫描器自检 / 首句定位 / 中英对齐 / 数字边界 / W4 口径 / 索引与阶段表 / 归档不变） |
| `docs/design.md` / `README.md` | 阶段表与索引更新 |

索引与阶段表断言集中在 `tests/test_w5_w6.py::TestIndexContract`，
`tests/test_project_contract.py` 在 W5–W6 那轮**一行不改**（W1–W3 的守门断言原样保留）；
2026-11-09 更新 W4 口径时只改了其中「未做」计数那一条，W1–W3 断言仍不动。

`gateway/` **零改动**；`GatewayConfig` 零字段新增；`config_hash` 与两组 W1 归档三件逐字节不变；
P1 仓库零改动；公开状态、remote、W4 全部不碰（用户明确要求等全部做完再统一处理）。

## 9. 本轮新增的「不许说的话」（叙事层专属）

<!-- FORBIDDEN-EXAMPLES-START -->
- ❌「做了一个面向用户的智能客服 Agent」
- ❌「W3 证明护栏有效 / W4 验证通过」
- ❌「两个项目可以合并成一个通用 Agent 平台」
- ❌「跑完 W4 就能得到正向 pass^k 结论」
- ❌「英文 README 里可以写中文版没有的能力」
<!-- FORBIDDEN-EXAMPLES-END -->

- ✅「这是一套评测装置 / 运行时平台，非面向用户的 Agent 应用；两个项目共用
  『配置即自变量 + 单变量对照 + 逐字节可复现』的方法论，但仓库与 commit 历史独立」
- ✅「所有数字都带边界；W4 两格已执行一次但未产收益结论、Docker 未实跑、真实供应商默认关闭，缺口如实讲」
