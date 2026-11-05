# 取数主流程（closed / open 通用；进入分析链路后先读本文件）

> 本文件承载 Step 1-2 的完整操作细节与执行纪律。命令形态已固化——**直接照抄
> 替换参数执行**，禁止先 `--help`、读脚本源码探测用法。

## Step 1 · 确认目标库

```bash
python -m jiuwenswarm.cli.dwcli datasource current
python -m jiuwenswarm.cli.dwcli datasource probe --db-ref <ref>
```

用户没给库就用 `datasource current` 的结果；没配置就提示配置，**不要默认/回退到
本地 .db 文件**。记下 source_id（后续 probe/join/execute 用）。

## Step 2 · 四层取数

全部经 `{analysis_skill_dir}/scripts/analysis_flow.py`（确定性 CLI，预算硬执行）。
绝对路径按运行时根目录解析——命令一律写成：

```bash
python "${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}/agent/workspace/skills/dw-analysis/scripts/analysis_flow.py" ...
```

（shell 里变量默认值不能用 `~`，写 `$HOME`；须展开成绝对路径，不要用 `$(pwd)` 猜）
**禁止 glob/搜索定位本脚本**。**同步阻塞**调用，禁止后台执行。

### 2.0 建会话

```bash
python scripts/analysis_flow.py init --question "<question_std>" --meta-file <work>/intent.json
```

- `--question` 传 Step 0.5 的标准表述（召回吃 std 提命中率）；`question_raw`
  在 `intent_normalized` 里对账，永不丢
- `--meta-file` 传 Step 0.5 产物（**写文件传参**，禁止内联）；脚本校验
  `question_raw` 必填、`intent` 六值枚举，入 manifest 后追踪页可见
- lookup / report_only 两路不 init、不产 manifest

记下返回的 `analysis_id`，后续所有子命令都带它。

### 2.1 L1+L2 · 一次拿全上下文

```bash
python scripts/analysis_flow.py context --analysis-id <id> --question "<question_std>"
```

返回 `route` + 四路召回（含每路 channel_status）+ 三子包（glossary /
metrics_candidates 含指标口径与维度白名单 / schema_tables 含表列与维值候选）+
`ambiguities` + `gap_terms` + `packs.knowledge_assets` / `knowledge_rules`。
拟计划前核对这些约束，规则存在冲突时澄清；`knowledge_rule_trace.status=unavailable`
表示未能完整取得规则，不能声称已核验全部约束。

**先看 `route`，它决定 2.2 怎么拟计划**：

| route | 含义 | 你要做什么 |
|---|---|---|
| `full_context` | 授权源全量 schema 已在 `packs.schema_tables`（全部表列与 join 边），四路召回均 skipped | 从全量语义及知识候选中拟计划，按本次问题选指标，不能把全部资产当成选定项；**表列结构已在包内、无需再探查**；维值候选不在包内（values 通道 skipped），过滤值标 `pending`，用一条 `probe --targets` 批量确认 |
| `term_shortcut` | 术语精确命中且唯一链到一个已发布指标（最硬信号） | **不做候选甄别**：`selected_metrics` 直接取 `packs.metrics_selected` 的 metric_key，**禁止另选、禁止编造**；`metrics_candidates` 为空不是异常 |
| `full_recall` | 术语不够格（理由见 `route_reasons`），走完整四路召回 | 从 `metrics_candidates` 候选榜里选 |
| `full_recall` 且 `route_reasons` 含 `no_metric_candidate` | 指标零候选——指标体系覆盖不到 | 落 2.3 探索（schema 探索），产物标探索口径；`gap_terms` 非空时按 2.4 提示建模 |

- **短路路径的过滤值全是 pending**（该路不跑维值路）→ 计划里照常标 `pending`，
  该成分 trace 记 `explore`，用**一条 `probe --targets` 批量确认全部待定值**
  （整批算一轮探索，逐列循环会一列一轮烧光预算）；probe 拿不到的值问用户，不猜
- **歧义**（ambiguities 非空）→ 触发 clarify 前先读 `references/clarification.md`；
  **clarify gate**：歧义未消解时 `plan` 会被脚本拒绝——用户确认后带
  `--clarified "<用户确认的口径>"` 重新提交
- **术语回源**：用户补充口径后，若术语库有对应术语，**以术语定义为准逐字核对
  基座口径**（定义中的限定词——状态过滤/去重规则/时间口径——逐字落到成分
  SQL）——口径以术语库为单一真相源
- **口径继承的唯一来源是问句命中的术语/指标**：召回的指标与术语自带口径限定
  （状态过滤、去重、汇总规则）——问句措辞命中术语或点名指标时按其定义执行；
  问句用**未命中**的字面业务词时按字面语义裸取，禁止从候选指标或兄弟成分
  借口径（问「支付金额」就不套用召回指标自带的「有效订单」过滤，除非问句
  自己带「有效/成交」类限定词）。指标口径与问句口径不是一个东西
- 通道状态只是参考信号（ok/no_hit/unavailable/**skipped**，skipped = 短路路径
  没叫这路，不是坏了），**不据此替用户决定分支**

### 2.2 L2④ · 产出 QueryPlan（你提议，服务端校验）

先判问题类型：问句点名**具体指标和/或维度的取数/对比/排名/分布**（「各州 GMV」
「8 月 AOV」）为 `mode=closed`（≤5 个）——**即便句尾带「出一份分析报告」**，报告
只是呈现形式；**不点名具体取数范围、只给主题要报告/复盘**（「出一份经营分析
报告」「全面复盘」）为宽问题，仅当开关开启（init 返回 `open_analysis_enabled: true`）
才用 `mode=open`（≤8 个，字段见 `references/open-flow.md`）。**开关开启时宽报告
问题必须 open**：核心所问拆 ≤3 个 core 成分，品类/供应商/地区等铺开面一律
optional facet——把 5 个分析面全声明为 required core 的 closed 计划是路由错误。
时间范围只是过滤条件，**不构成 closed 判据**。开关缺失或 false 时宽报告问题同样
走 `mode=closed`。**开放模式的完整字段契约见 `references/open-flow.md`**。

**JSON 一律写文件后传 `--plan-file`**（Windows 下内联 JSON 会被 shell 引号/中文
破坏导致解析失败）：

```bash
python scripts/analysis_flow.py plan --analysis-id <id> --plan-file <work>/plan.json
# 或写文件后管道 stdin（--plan-json -）
```

> **禁用** `--plan-json '<内联 JSON>'`：单引号在 cmd/PowerShell 非定界符、中文转义
> 易破坏。全流程中间结构（plan / traces / selected / time / temp-scope）同理：
> **写文件传 `--*-file`**，不用内联 `--*-json`。

plan 结构（components 数组，每个）：

```json
{"component_id": "c1", "text": "<成分描述>",
 "selected_metrics": ["<候选榜内的 metric_key>"],
 "derived_metrics": ["<报告层派生计算的 metric_key>"],
 "dimensions": ["<schema 子包内的列>"],
 "time": {"dimension": "<列>", "start": "YYYY-MM-DD", "end_exclusive": "YYYY-MM-DD"},
 "filters": [{"field": "<列>", "op": "=", "value": "<值>", "status": "confirmed|pending"}],
 "depends_on": [], "output": "<输出粒度/单位>",
 "sql_draft": "<该成分的首版 SQL——与计划同轮产出，直接进入 2.3 的 check>"}
```

- **intent=diagnosis 时的成分骨架建议**（引导非强制）：c1 = 总指标时间序列
  （北极星指标，日粒度，供趋势/异常检测）；c2..cn = 归因维度分组序列（候选贡献
  维度各一条 GROUP BY）；ratio 类指标的分子分母各自成序列——现状取完数到报告
  阶段才发现缺分维度序列要回炉重跑 plan，骨架引导的目标是一轮取全
- **selected vs derived 分工**：`selected_metrics` = SQL 直接产出的指标（ratio 类
  的分子分母必须都在 SQL 里）；`derived_metrics` = **报告层计算的派生指标**
  （如「客单价在报告层用 gmv/订单数算」——放 derived，**不要**放 selected，
  否则口径检查会因 SQL 缺分子分母而失败）
- **核心产出量直接进产物列**：问题直接要的量（含比率/占比）作为 SQL 列一并
  SELECT——比率形如 `SUM(a)/NULLIF(SUM(b),0)`，分子分母列同时保留；量纲换算
  （如 ×100 转百分比）统一推迟到呈现层，产物层保持原始小数。只取中间量、把
  最终计算全推迟到报告层，会让报告与图表消费不到所需的列（图表轴只能绑 CSV
  列），被迫回炉重跑。报告层**补充性**的派生量不受此限
- **一条 SQL 能回答的成分不要拆**：分组聚合、加过滤、算多个度量都是单成分
  一条 SQL 的事——「按地区统计金额分布」是一个成分，不是「每地区一个成分」。
  拆分的唯一理由是**必须分别取数**（不同表/不同时间窗/有依赖的中间量）
- **成分行数先估上界**：GROUP BY 键组合的行数上界 ≈ 各维基数之积——维值×
  时间的全叉积动辄数千行，超过产物行数上限即被截断，截断数据进证据链就是
  失真（下钻结论建立在部分数据上）。上界过大的成分先收敛粒度（只留结论
  需要的分组）或按核心度量取 TopN，再进产物
- `selected_metrics`/`derived_metrics` 只能从 `packs.metrics_candidates`
  （`full_recall`）或 `packs.metrics_selected`（`term_shortcut`）里选，**一个都不许编造**
- 过滤值来自维值候选标 `confirmed`；没命中的标 `pending`（待确认）
- 指标体系覆盖不了的成分放进 `uncovered`，不要硬凑指标
- **`sql_draft` 与计划同轮写出**（充分性判断为 sufficient 且编译通道未覆盖的
  成分）——SQL 只能引用 `packs.schema_tables` 列出的表和列，**写之前逐字核对
  列名**（抄原文，不要按语义自造列名）；有选定指标的成分先走 2.3 ⓪ 编译
- 校验 400 的错误摘要**直接打在 stderr**（逐条 [成分/字段] 信息），按摘要修正后
  重提，**最多 2 次**——**禁止 read/grep bash 输出文件再找一遍 errors**
- plan 响应中的 `knowledge.components[component_id]` 是该成分的规则和样例上下文。
  完整遵循适用规则；只在 LLM SQL 生成路径参考兼容样例，G1 编译不消费样例

### 2.3 L3 · 逐 component 执行（**批处理优先**）

**⓪ G1 编译通道（有选定指标的成分优先走，零 LLM）**：

```bash
python scripts/analysis_flow.py compile-batch --analysis-id <id> --batch-json '[{"component_id": "c1"}, {"component_id": "c2"}]'
# 单成分才用：compile --analysis-id <id> --component-id <cid> [--source-id <sid>]
```

- `covered=true` → 该成分**到此完成**（编译+执行+CSV+结果检查一条命令），产物权威
  ——**不需要 sql_draft、不需要 check**
- `covered=false` 看 `action`：`probe_first`（过滤值 pending）→ 先 probe_values
  确认值再重跑 compile；`fix_plan`（跨表/维度越界）→ 拆成分/取消选定转探索/
  留作建模建议——**不要**把越界成分硬塞给 LLM 生成（check 白名单照样拦）
- **≥2 个有指标成分必须用 compile-batch 一轮提交**
- **分组/粒度需求在 plan 里一次声明**：编译按 plan spec 产出——spec 不声明
  `dimensions`/`time.grain` 时得到的是单值标量；预期「每个维度一行」输出的
  成分必须在 plan 阶段写全分组声明，事后发现缺分组再手工重写 SQL 重跑是
  双跑浪费

**轮次纪律（时效红线）**：无依赖的成分**批量处理**——一轮生成全部 SQL、逐条
check、失败的一轮全部重写。**禁止**每个成分单独循环（3 成分串行 = 3 倍轮次）。

**① 充分性判断**（多成分一条命令批量记）：

```bash
python scripts/analysis_flow.py trace --analysis-id <id> --traces-json '[{"component_id":"c1","decision":"sufficient"},{"component_id":"c2","decision":"explore","missing":[{"kind":"value","what":"region 取值"}]}]'
```

- decision ∈ `sufficient` / `explore` / `clarify` / `infeasible`
- **缺业务口径只能 clarify**（读 `references/clarification.md`）；缺物理信息 → explore

**② 探索（≤3 轮，超限脚本拒绝）**——首轮前读 `references/exploration.md`
（三段顺序/空结果回退/留空不猜），按缺失项自选工具：

| 工具 | 命令 | 用途 |
|---|---|---|
| **先查已有上下文** | manifest 的 `context.packs.schema_tables` / recall 命中 | 表列信息**优先从 context 子包取**——list_tables/describe 只在 context 没覆盖该表时用；同表不查两次 |
| recall_more（**优先**） | `context --analysis-id <id> --component-id <cid> --question "<成分文本>" --mode recall_more` | 重跑四路召回补子包，不碰活库 |
| list_tables | `list-tables --analysis-id <id> --component-id <cid> [--db-ref <完整指针>]` | 授权 source 内表清单。`--db-ref` 可整段省略；传入须为 datasource current 返回的完整指针，**裸库名会被拒**。`row_count` 为 `null` = 未统计（sqlite 等无元信息库不做全表扫描），**不是空表** |
| describe_table | `describe --analysis-id <id> --component-id <cid> --table <t1> [--table <t2> …] [--db-ref <完整指针>]` | 表 DDL/列注释 + **真实 row_count**（表清单里 row_count 为 null 时用它确认有无数据）；**一次可查多表**（重复 `--table` 或逗号分隔），多表只记 1 轮探索——查 3 张以上表时用多表形态，避免撞 3 轮上限 |
| probe_values | `probe --analysis-id <id> --component-id <cid> --source-id <sid> --table <t> --column <c> --like "<模式>"` | 受限取值确认（敏感列禁用） |
| check_join | `join --analysis-id <id> --component-id <cid> --source-id <sid> --left t1.c1 --right t2.c2 [--sample]` | join 类型/包含率；未确认一律推断级 |

**探索预算超限的降级动作**（脚本拒绝并提示时）：立即停止一切探索调用，**基于
已收集材料直接手写该成分 SQL 走 check→execute**（G3，`uncovered` 声明「探索受
轮次预算限制」）；连一张相关表都没找到时才 ask_user。**禁止空 final 收尾**。

**③ 生成 SQL → 程序化检查（批量并行）**：

```bash
python scripts/analysis_flow.py check-batch --analysis-id <id> --batch-json '[
  {"component_id": "c1", "sql": "<SQL1>", "selected": [{"metric_key": "<key>"}], "time": {...}},
  {"component_id": "c2", "sql": "<SQL2>"}]'
```

- 2.2 已产出 `sql_draft` 的成分**直接提交 check**（不再重新生成）；其余 sufficient
  成分一轮全部写出，一次 `check-batch` 提交
- 有选定指标的成分**必须**带 `selected`（触发口径一致性检查）
- 用过临时 scope 带 `"temp_scope": {"tables": {"<表>": ["<列>"]}}`
- check fail 按 `retry_hint` 修正——失败的 SQL 一轮全部重写，最多 2 轮；仍失败
  该成分标失败，**不能通过探索绕开指标定义**
- **SQL 硬约束**：只能引用 `packs.schema_tables` 列出的表列与维值候选——生成后
  逐字核对每个列名（防幻觉列名触发整轮重写）；时间边界**左闭右开**（`>= AND <`）

**④ 统一执行（批量并行）**：

```bash
python scripts/analysis_flow.py execute-batch --analysis-id <id> --batch-json '[
  {"component_id": "c1", "sql": "<检查通过的 SQL1>", "source_id": "<sid>", "selected": [...], "time": {...}},
  {"component_id": "c2", "sql": "<SQL2>", "source_id": "<sid>"}]'
```

返回逐成分 `csv_path` + `grounding_level` + `result_checks`。**结果检查有 fail
（空结果/分组键重复/时间越窗）→ 先解决数据问题，不产出业务归因**；返回里的
`hints` 字段附带按状态的处置建议（空结果回退/截断明示），照做。

**⑤ 失败处理**：infeasible / 检查失败 / 预算超限 → 更新计划（显式标注失败原因），
**不静默换口径**；部分结果也是合法交付。

**预算纪律（脚本硬执行）**：closed component ≤5、open ≤8；required 探索 ≤3 轮、
optional ≤1 轮；默认时限 90 秒；生成重试 ≤2、SQL ≤15 条、analysis_flow 工具调用
总数 ≤30。LLM 调用总数：**≤2 成分 ≤20；3~5 成分 ≤ 1 + 成分数 × 6**——多成分
批量提交（check-batch / execute-batch / 批量 trace）是达标关键。Step 0.5 的
1 次意图判定计入总额（init 已起算为 1）。

### 2.4 未完成成分

`uncovered` 与失败成分在报告里显式列出（标「未完成」与原因），附 missing 清单。
`gap_terms` 非空且 `metrics_candidates` 为空时，答复里点破建模缺口（话术见
`references/delivery.md`）。

---

## 执行纪律（全流程强制）

- **零侦查（第一优先级）**：进链路第一步固定为 `datasource current`——此前禁止
  `ls` 目录、读 metadata/config/yaml、grep 摸底。
  `analysis_id` 由 init 的 stdout 返回，**禁止 ls artifact 目录寻找**
- **同步阻塞**：所有脚本调用等结果返回，禁止后台执行
- **禁手动编码前缀**：平台已注入 UTF-8（`PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`）
  ——自己加前缀纯属多余且大量失败；直接 `python <脚本> <子命令> ...`
- **信任脚本输出**：stdout JSON 就是结果——禁止再读回 manifest/CSV/ctx 文件、
  **渲染产物与报告文件**、禁止测试终端编码、禁止 `python -c` 二次解析
- **fail 只看 retry_hint**：只依据 check 返回的 `retry_hint`/`checks` 明细修正
  ——禁止 grep、读服务端/脚本源码、写试验 JSON 逐个试参；retry_hint 不足时
  标注失败继续或向用户澄清——**调试不是你的职责**
- **重试上限**：同一条命令连续失败 **≤3 次**即停止——3 次仍失败停下、原样报告
  stderr 首行、让用户判断
- **CSV 路径接力**：execute 返回的 `csv_path` 原样传给 data-insight 或 dw-report
- **一条 SQL 只干一件事**：切片/中间量缺了再发一条，不要心算凑数
- **一条 SQL 口径不改写**：execute 后的 SQL 原文进报告素材，边界写法不得改写
- **manifest 是唯一账本**：预算、探索轮次、检查结果都以 manifest 为准
  （`manifest --analysis-id <id>` 可随时查看）
