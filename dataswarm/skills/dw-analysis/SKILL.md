---
name: dw-analysis
description: 数据分析与洞察类问数的**唯一入口**（含完整取数——分析意图的问句
  禁止先走 nl2sql 再拼分析，绕行会丢失口径账本）。当用户提出分析/洞察类诉求——
  看趋势、出经营分析报告、为什么涨了/跌了、哪个地区/渠道拖累或推动了变化、
  指标异动归因、相关性分析、异常数据怎么看、出分析报告时使用。
  入口先做场景判定与意图规范化（Step 0.5 六类分流：纯取数/报告整理/定量
  计算/探索/纯分析/归因诊断——轻任务短路、重任务进四层链路），再按 intent
  走四层取数（L1 三级漏斗召回 → L2 QueryPlan → L3 逐成分执行 → 报告），
  归因增强按信号叠加（不再二选一）。报告按 G1/G2/G3 分级（编译产物权威、
  LLM 生成路径非权威）；目标数据库由工作空间数据源决定，不写死库名；
  data-insight 只消费 CSV。
version: 2.5.0
tags: [analysis, insight, attribution, nl2sql, report, metrics, recall]
allowed_tools: [bash, read_file, write_file]
---

# dw-analysis —— 数据分析与洞察端到端编排（四层链路）

**分析/洞察场景的单一入口**。不自己做统计计算、不自己画图，按固定流程串联
四层链路与下层 skill，把「问数 → 取数 → 洞察 → 报告」固化成可重复的流程。

## 什么时候用它（Step 0 · 场景判定）

| 场景 | 触发词（任一命中即进入） | 典型例句 / 边界例 |
|---|---|---|
| ✅ 分析场景 | 分析、统计、趋势、走势、经营分析、日报/周报、看板、分布、对比、占比、排名、排行、TopN、汇总、多维、想看看 X 的关系、出份报告 | 「分析 3 月 GMV 趋势」；边界：「看看这个月的情况」（疑似分析，放行让 Step 0.5 判） |
| ✅ 洞察场景 | 为什么涨了/跌了、是哪个地区/渠道/品类的问题、异动归因、异常、什么影响了 X、X 和 Y 有关系吗 | 「为什么这个月销售额跌了」；边界：「查一下 DAU 并分析下降原因」（复合句整句放行，Step 0.5 就高判主意图） |
| ❌ 不进入 | 建表建仓/写任务（→ `dw-warehouse-design` + `dw-warehouse-build`）、强信号纯查数（→ 直接 `nl2sql`） | 「查一下上月销售额」「这个客户有哪些订单」（零分析词汇取数 → nl2sql）；「这数据质量怎么样」（探索，放行进 Step 0.5） |

非分析/洞察场景**不要**链路化硬套本 skill——把请求转交对应 skill。

**无人值守/评测场景禁 `ask_user`**（本步就要判定，别拖到报告阶段）：口径缺口
自行决策并在报告「口径与局限」段明写假设——无人应答的环境里提问只会空等烧轮次。

**放行规则**：触发词命中、疑似分析、复合句带任何分析成分、或欠指定但明显
依赖数据解读（「最近卖得怎么样」没说指标名也算合格）——一律进 Step 0.5，
由意图分类定去留。「强信号纯查数」=「查一下/列出/多少 X」句式**且零分析
词汇**，只有这类才在 Step 0 直接转 nl2sql；拿不准的放行（放过只是多一轮
判定，漏判要走回头路）。

## Step 0.5 · 意图理解与规范化（进链路前一轮完成，不调用工具）

对 Step 0 放行的问题，先完成意图分类与保守改写，产出 `intent_normalized`
（五字段 JSON），再按分流表决定链路深度。六步按序执行：

1. **预清洗**：口语→书面语且保持开放表述（「咋样」→「整体表现」，**禁止**
   改成「趋势/对比」——那是替用户选了分析角度）；代词从对话上文消解，
   支撑不了登记 missing 不猜；复合句按连接词（并/同时/顺便）拆子诉求清单；
   指标词逐字保留用户原词进 `metrics_mentions`（**不做**指标名归一）
2. **时间口径规范化**：「上月」→ `period + basis=calendar_month`；「最近
   一个月」→ `basis=rolling_30d`；完全没提时间 → `basis=default` 且
   `defaults_applied` 登记「时间口径按近30天」；「旺季」等业务时段无法
   规范化 → missing 登记，不硬翻
3. **复合意图预检**：子诉求逐个粗判，就高取主 intent（优先级
   diagnosis > analysis > computation > exploration > lookup > report_only）；
   **低阶意图不丢弃、不拆趟**——降级为报告的内容成分（「查 DAU 并分析原因」→
   主 intent=diagnosis，「查 DAU」是 c1 总量序列成分）
4. **六类有序分类**（单句按序逐类检查，命中即停）：

| # | intent | 判定特征 | 典型例句 |
|---|---|---|---|
| 1 | `lookup` | 单值/单批记录、单维聚合/TopN/环比，无解释诉求、无归因、无多成分拆解 | 「昨天 DAU 多少」「这个客户的订单」 |
| 2 | `report_only` | 会话已有本问题结论，新诉求只是格式化交付 | 「把刚才的分析整理成报告」 |
| 3 | `computation` | 公式明确，输入→公式→输出 | 「算一下这个月客单价」 |
| 4 | `exploration` | 无预设业务问题，看数据本身（质量/分布/可用性） | 「这数据质量怎么样」「age 分布为什么怪」 |
| 5 | `diagnosis` | 问为什么/谁拖累/异动归因 | 「为什么跌了」「哪个渠道的问题」 |
| 6 | `analysis` | 看趋势/分布/对比/汇总/报告（兜底主路） | 「分析 3 月 GMV 趋势」「出月报」 |

5. **模糊裁决**（两类拿不准按此取向）：取数 vs 分析 → `analysis`（空手而归
   比多跑链路更贵）；归因 vs 分析 → `diagnosis`（归因多取的分维度数据对
   纯分析也有用，缺了要回炉重跑 plan）
6. **组装五字段 + 自检三问**（任一不过原地修正再出，不带病下游）：
   ① question_std 里的意图词（趋势/对比/分布）原话有依据吗？
   ② metrics_mentions 与原话术语逐字一致吗（被润色过的还原）？
   ③ 填过的默认值都登记进 defaults_applied 了吗？

**intent_normalized 产物**（五字段，`question_raw` 永不改写——它是审计
基准与召回兜底入参）：

```json
{"question_raw": "<原话>", "question_std": "<标准表述>", "intent": "<六值之一>",
 "metrics_mentions": ["<用户原词>"],
 "scope": {"time": {"period": "<…>", "basis": "<calendar_month|rolling_30d|default|custom>"}, "filters": []},
 "completeness": {"missing": [], "defaults_applied": []}}
```

**分流表**（按 intent 执行，意图识别是成本闸门——轻任务不付重链路的钱）：

| intent | 动作 |
|---|---|
| `lookup` | 已发布指标可直接回答 → 按下方「路径判定」`metric-v2 query` 权威直答；无指标覆盖的取数 → `question_raw` 透传 nl2sql（不 init 不产 manifest） |
| `report_only` | 结论在场检查（以 manifest 为准）→ 直接触发 dw-report |
| `computation` | 走主流程但 plan 仅单成分（`context` 照走锚定口径，`sql_draft` 同轮即终稿），执行后数字直答；check/execute 安检**不豁免**，ratio 类分子分母同一条 SQL |
| `exploration` | init → trace(explore) → list-tables/describe/probe（≤3 轮照记）→ 需要分布统计时单成分 plan → 产物全标探索口径（G3） |
| `analysis` | 完整四层链路 → Step 3 报告组装（分析基线） |
| `diagnosis` | 完整四层链路（成分骨架引导）→ Step 3 报告组装（分析基线 + 归因增强） |

**路径判定（混合路由，Step 0.5 判定后执行）**：

- **单指标纯取数**（单维聚合 / 过滤 / TopN / 环比，已发布指标可直接回答）→
  **不进四层链路**：直接 `python -m jiuwenswarm.cli.dwcli metric-v2 query` 确定性
  查询（权威、快、query_log 自带审计）。**答复必须含「权威口径」或
  「authoritative」或「已发布」字样**并注明所用指标 key——漏标即不合格交付
- **分析性产出**（报告 / 图表趋势 / 归因解释 / 多成分拆解）或**无指标覆盖**
  （维度不在白名单、口径未发布、临时口径）→ **必须进四层链路**（manifest 账本，
  不可绕过——分析追踪页的审计以 manifest 为准）

## 设计边界（四条铁律）

1. **不写死数据库**。目标库一律由 `dwcli datasource current` 返回的当前工作空间
   业务库 ref 决定。**禁止**在文案、SQL、脚本参数里硬编码库名/连接串。
2. **双路径权威分级**。有选定指标的成分先走编译通道——编译产物由已发布定义
   确定性渲染，`authoritative=true`（G1，报告标「权威口径」）；编译不覆盖的
   成分走 LLM 生成路径，全部非权威，标「探索口径」。报告声明写清两类边界；
   LLM 路径的 G1 数字标「参考已发布口径」，不能包装成官方数。
3. **data-insight 不取数**。取数由本 skill 四层链路完成；`data-insight` 只吃 CSV
   路径做统计计算。**不**给 data-insight 传库名/连接串。
4. **dw-report 只出报告不交付**。落盘到 `${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}/artifact/analysis/`，
   `send_file_to_user` 是本编排层的职责。

## 路径文档导航（按需只读命中项，其余不读）

| 时机 | 只读这份 |
|---|---|
| 进入 Step 1-2 取数（所有分析类） | `references/fetch-flow.md` |
| mode=open 宽报告（计划字段/facet/core 指标路径） | `references/open-flow.md`（+ fetch-flow） |
| 触发归因增强（见 Step 3 条件） | `references/insight-path.md` |
| 某个成分 decision=explore | `references/exploration.md` |
| 触发 clarify（缺业务口径） | `references/clarification.md` |
| 进入 Step 3 报告与 Step 4 交付 | `references/delivery.md` |

## Step 1-2 · 四层取数

**进入取数第一步：读 `references/fetch-flow.md`**（强制——plan 契约以该文档为
准，服务端按 schema 校验，自造字段必被拒绝）。
文档里 plan 的 components 契约（`component_id/text/dimensions/time.dimension/start/
end_exclusive/output/sql_draft`）、execute 的 source 用法、批量纪律全部照抄。

按 `references/fetch-flow.md` 执行（Step 1 确认目标库 → 2.0 建会话 → 2.1 上下文
与 route 判定 → 2.2 QueryPlan → 2.3 逐成分执行 → 2.4 未完成）。本节只保留三条
红线：**命令形态照抄不探测**；**零侦查**（第一步固定 `dwcli datasource current`）；
**同步阻塞**。

**脚本调用形态（固定，禁止探测）**：

```text
python "${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}/agent/workspace/skills/dw-analysis/scripts/analysis_flow.py" <子命令> ...
```

**禁止** `python -m jiuwenswarm.skills.dw_analysis.scripts.analysis_flow`（skills 不在
Python 包内，必报 `ModuleNotFoundError`）。用错形态后**不要放弃链路转手工取数**——
手工取数丢失 manifest 账本与审计；按上面路径重试一次即可。

**Windows 传参纪律（七条）**：

1. JSON 参数一律 `--*-file <文件路径>`（**先 write_file，再在命令里引用**；
   禁止先跑后写造成 `No such file`）；内联 JSON / 中文串在 Windows shell 下会被
   引号与转义破坏。
2. sqlite 的 `--db-ref` 路径一律正斜杠：`sqlite://D:/path/to/x.sqlite`；
   `--db-ref` 也可整段省略（自动用当前工作空间业务库），**禁止自造 source_id**
   （`--source-id biz` 之类必被授权拒绝）。
3. 命令统一走 bash（`shell_type: bash`）；**禁止** powershell 跑 heredoc
   （`python - << 'PY'` 会被 PowerShell 当语法解析）或含花括号的 JSON。
4. **禁止传 `workdir` 参数**：默认工作目录已正确，手写路径极易拼错。
5. **改写已有文件用 `edit_file`**：`write_file` 覆盖已存在文件要求先全量
   `read_file`，只读片段就写会被拒——新文件用 `write_file`，
   改旧文件用 `edit_file`。
6. **SQL 一律 `--sql-file <文件>`，禁止内联 `--sql`**：bash 双引号里的反引号会做
   命令替换，``SELECT `col` AS x`` 到进程时已变成 `SELECT  AS x`（列名清空 →
   guard 解析失败）。SQL 里的标识符用双引号或裸名；
   内联串含反引号时脚本会直接拒绝并提示。
7. **禁止用 `&&` 串联多个 analysis_flow 命令**：长链输出会被截断、轮次与预算
   失控。探索用 `describe --table a --table b`（多表 1 轮）；执行用
   `execute-batch`；查询用 `check-batch`——批量走各自的批量形态，不靠 shell
   串联。

## Step 3 · 报告组装：分析基线 + 归因增强

**所有分析类报告都有分析基线**（趋势/对比/分布/结构——数据已在 c1..cn 成分里）。
**归因增强按信号叠加**，不再由 intent 单选（任一命中就做）：

| 触发条件 | 动作 |
|---|---|
| intent = diagnosis | 归因增强 |
| 问题含驱动线索（为什么/谁拖累/下降原因/增长来源） | 归因增强 |
| c1 序列出现显著异常或拐点（趋势明显转折） | 归因增强 |

归因增强 = 进入 `references/insight-path.md` 五级路径：观测基准 → 异常确认 →
维度下钻 → 因子拆解 → 解读收口（每级产出决定是否进下一级；未发现异常不下钻；
无维度可拆时诚实写「未找到集中解释」+ 量化未解释比例）。metric_kind 从指标
定义推导（numerator/denominator 齐全 → `ratio` 拆分子分母；去重语义 →
`distinct`；其余 `additive`）。

归因结论 + 支撑数据一并作为上文素材，**报告一律经 dw-report skill_tool
组装**（禁止自建报告脚本）；spec 深度要素与字段契约以 dw-report
`reference/quality.md` 与 `reference/schema.md` 为准（本 skill 不重复）。

## Step 4 · 交付

**数据就绪 ≠ 完成**：报告生成 + `send_file_to_user` 才算交付。答复纪律
（2-3 条含关键数值的结论 / 口径两维标注 / 建模建议 / 规范化声明）见
`references/delivery.md`。

**交付门禁（强制）**：HTML 报告落盘后先过
`dw_report.py verify --html <path> --min-charts 1`（脚本路径见 dw-report
SKILL.md「三条命令」），非 0 退出禁止交付。`send_file_to_user` 的
`abs_file_path_list` 传**数组**，不要传 JSON 字符串。

**交付一次收口**：`send_file_to_user` 成功后本轮交付即结束——除非用户明确要求，
不得再渲染新版本、不得重复调用 `send_file_to_user`、不得改文件名重出「final」
版本。需要对已交付内容修正时，先向用户说明原因，再交付修订版一次。
