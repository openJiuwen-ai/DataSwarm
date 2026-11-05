---
name: dw-warehouse-design
type: execution
layer: L2
tags: [data-warehouse, intent-routing, minimal-lifecycle, csv-ingestion]
user-invocable: true
requires: []
handoff_to: [dw-warehouse-build]
description: |
  数仓快速设计（意图识别 + 最小生命周期版，只做设计不执行）。当用户提到"导入CSV/Excel/导入数据/入仓/只建ODS/建DWD/数仓分层/建数仓/从CSV或Excel建仓"时使用——目的地是数仓（建表搬数据），csv/xlsx/xlsm 均支持。
  用户只说"接入/连接数据"（无入仓意图）、数据库要当数据源直接分析时，走 dw-datasource-onboarding（仅注册数据源，不建表不搬数据）。
  先用规则分类器识别意图——纯数据导入只建 ODS 并导入（L1，不做 DWD/DWS/ADS 分层）；建模需求到 DWD（L2）；用户明确要求分层才做全量（L3）。
  设计阶段单次快照探查；DDL/ETL 由脚本模板确定性生成（LLM 只写语义规格 JSON，不手写 SQL）。
  本 skill 全程只读不写业务库，唯一交付物是 plan JSON（格式与 dw-warehouse-build 完全兼容）；用户确认后 handoff plan_id 到 dw-warehouse-build 任务化执行。
allowed_tools: [bash, read_file, write_file]
---

# dw-warehouse-design

数仓**快速设计**：意图识别 → 最小生命周期 → 确定性生成 → handoff 执行。

## 核心定位

| 做 | 不做 |
|---|---|
| 规则优先识别意图，按最小生命周期建仓 | 不加区分地全量四层设计 |
| 单次快照探查（方言 + CSV schema + 已有表） | 设计阶段反复多次探查调用 |
| LLM 只写语义规格 JSON（L2/L3） | LLM 手写 DDL/ETL SQL |
| 脚本模板确定性渲染 DDL/ETL | 每次生成结果不一致 |
| 产出 plan，等用户确认后 handoff plan_id | 执行建表/导入/ETL（转 dw-warehouse-build） |

## 生命周期

| Tier | 含义 | 产物 | 触发示例 |
|---|---|---|---|
| L1_INGEST | 仅导入 | ODS 建表 + CSV 导入 + 行数校验 | "把这些 CSV 导进来" |
| L2_MODELING | 导入 + 明细建模 | L1 + DWD | "清洗一下建明细层" |
| L3_FULL_DW | 全量分层 | L1 + DWD + DWS + ADS | "从 CSV 建数仓" |

**默认最小路径**：识别不确定时从 L1 起步，完成后提示可升级，不擅自扩大范围。

## 命令执行规则（必读）

- 命令须同时兼容 POSIX shell 与 Windows cmd，只写两者公共子集：禁止 `VAR=value cmd` 环境变量前缀、`&&`、`;`、`|`、`>` 等 shell 链接符、`$VAR` 引用、行尾 `\` 续行
- **含空格/中文的参数值必须整体用双引号包裹**（如 `--text "<用户原话>"`）；**禁止单引号**——单引号是 POSIX 专用，Windows cmd 把它当普通字符，原话会按空格拆散报 `unrecognized arguments`。双引号是 cmd / PowerShell / bash / sh 的公共引号子集，Windows/Linux/macOS 通用
- 用户原话含 `"`、`$`、反引号等 shell 敏感字符时（罕见）：保持语义不变，替换成中文引号或去掉该符号后再传；**禁止**反斜杠转义（cmd 与 bash 转义规则不兼容）
- 各脚本已内置 UTF-8 输出处理（stdout/stderr reconfigure），跨平台无需设置 PYTHONIOENCODING
- 每条命令单行执行，只调用一条 `python scripts/<脚本>`，不写中间包装脚本
- JSON 一律落文件传参（`--spec-file`），不用命令行内联大 JSON

## 流程

### Step 1 — 意图分类（所有请求必做）

```
python scripts/intent.py --text "<用户原话>"
```

`--text` 的值必须整体放在**双引号**里（含空格/中文时单引号在 Windows 会拆散参数，见命令执行规则）。

- `action=BUILD` → 按 `tier` 走 Step 2；`confidence<0.8` 时先用一句话向用户确认
- `action=EXECUTE` → 直接 Step 4（需会话中已有 plan_id）
- `action=APPEND` → 增量追加也走本 skill 设计流程：Step 2 快照确认已有表与复用标记 → Step 3 只为**新表**写语义规格（已有表不进 spec，由 discovery 标记复用跳过）→ 生成只含新表的 plan → Step 4 确认门；确认后按 Step 4 的 APPEND 交接执行，**严禁**对本次生成的新 plan_id 执行 `run`
- `action=UNKNOWN` → LLM 语义兜底：agent 基于用户原话预判意图（仅导入 / 建模到 DWD / 全量分层 / 执行方案 / 追加表 / 无关），预判为 BUILD 类时用一句话**带预判**向用户确认（例：『看起来您是要把 CSV 接入数仓，只建 ODS 贴源层，对吗？』），确认后按对应分支继续；完全无关才礼貌收尾。LLM 预判不能替代用户确认，也不得直接扩大层级

### Step 2 — 单次快照（1 次调用）

```
python scripts/discovery.py --csv-dir <目录> [--files a.csv,b.csv]
```

返回方言、业务库、文件 schema（类型/行数）、已有表与复用标记。目录同时支持 CSV 与 Excel（xlsx/xlsm，`--files` 也可指定 .xlsx）；CSV 每文件自动嗅探分隔符（`,` `\t` `;` `|`）与编码（utf-8/gbk）随 plan `source_config.file_options` 传递给导入任务（xlsx 无分隔符/编码参数）；Excel 列类型由 duckdb `read_xlsx` 嗅探。列可空性与十进制精度均按**全文件扫描**判定（非采样）：NOT NULL 只基于全量事实（避免空值行被 Stream Load 静默过滤）；金额/十进制列默认 `DECIMAL(P,S)`（精确聚合，无浮点累积误差），科学计数法/超精度回退 `DOUBLE`。失败时按返回的 error 处理，不重试探查命令。

**方言感知**：快照返回的 `dialect`（doris/mysql/sqlite/duckdb）随 plan 传递。`dialect=mysql` 时 plan.py 渲染 InnoDB 风格 DDL（类型自动映射，如 `STRING`→`TEXT`）；`dialect=duckdb` 时渲染双引号标识符 DDL（无 DUPLICATE KEY/分桶/列内 COMMENT，类型仅 `LARGEINT`→`BIGINT`），ODS 导入走 `CREATE OR REPLACE TABLE ... read_csv()/read_xlsx()` CTAS 物化到 workspace warehouse（csv 与 excel 源同链路）。语义规格仍统一写 Doris 风格类型（见 `references/semantic-spec.md`），方言差异由渲染器处理。

### Step 3 — 生成方案

**L1（方案生成全自动，无需 LLM 设计）**：

```
python scripts/plan.py --action plan --csv-dir <目录> --tier L1
```

**L2/L3（LLM 写语义规格，脚本渲染 SQL）**：

1. 生成规格模板（可选）：`python scripts/plan.py --action spec-template --csv-dir <目录> --tier L3`
2. 按 `references/semantic-spec.md` 写 `<workspace>/tmp/_spec.json`（只写语义，不写 SQL）。业务诉求是**看某些指标**时优先用指标驱动模式：在 spec 里声明 `metrics[]`（指标名/口径/粒度/表达式/来源），DWS/ADS 分层表由脚本自动推导，无需手写；粒度列类型/可空性自动继承来源，空值清洗用 `grain_exprs`（如 COALESCE）
3. 构建：`python scripts/plan.py --action plan --csv-dir <目录> --tier L3 --spec-file <spec路径>`

**规格契约边界**：字段名与语法以 `references/semantic-spec.md` 为**唯一真源**——**禁止**通读 `scripts/` 源码核对字段（内部实现字段可能与 JSON 不同名，如 join 的 JSON 字段 `type` ↔ 代码内部 `kind`）。JOIN 渲染自动用 `别名.列` 全限定，无同名列歧义。

**调度跟随用户输入**：用户提出调度诉求（如『每天凌晨 3 点跑』）时，L2/L3 写入 spec 顶层 `schedule_cron`（5 段 cron：分 时 日 月 周），L1 用 `--schedule-cron` 传参；未提调度则缺省 `0 2 * * *`（每天 02:00 T+1）。方案摘要会展示调度供用户确认。

输出 plan_id + 分层表摘要。`--dry-run` 只预览不落盘。规格校验失败按 errors 修规格，不改脚本。

### Step 4 — 输出方案，等待确认后 handoff

**完整方案正文必须先输出到当前 assistant 回复正文，然后结束本轮等待用户确认**——仅在 `ask_user`、终端输出或工具结果里出现不算呈现。L1/L2/L3 全部执行相同确认门（L1 的“全自动”仅指方案生成无需 LLM 设计，**不豁免确认门**）：

1. **输出完整方案并停止**：`read_file` 读取 plan 文件（`<workspace>/plans/<plan_id>.json`）的 `confirm_question` 字段（已内嵌完整方案正文、`plan_id` 和确认问句），在当前 assistant 回复正文中**原样**输出该字段值。可以在其前加固定标题（如 `## 待确认方案`），但不得删改、摘要或重建正文。输出后必须立即结束当前 turn；不得继续调用工具、生成执行任务或 handoff。
2. **告知回复方式**：在方案正文后用普通文本告知：回复 `确认`、`执行`、`确认执行`、`OK` 等表示同意的内容即可执行；回复 `调整方案：<调整内容>` 修改后重新生成并再次完整呈现；回复 `取消` 放弃执行。
3. **下一轮处理确认结果**：存在本轮待确认方案和唯一 `plan_id` 时，**直接由大模型结合上一轮完整方案、确认问句和当前会话状态判断用户回复的语义**。用户回复表达确认或同意执行（包括但不限于 `确认`、`执行`、`确认执行`、`OK`、`好`、`可以`、`没问题`、`就这样` 等同义表达）时，视为对上一轮方案的确认，直接执行 handoff（按本次会话的意图分 BUILD / APPEND 两条路径）：

   **BUILD（全量新建）** → 传 plan_id 给 `dw-warehouse-build` 任务化执行（建任务/DAG/publish/血缘/导入）：

   ```
   python <skills>/dw-warehouse-build/scripts/execute_pipeline.py --action run --plan-id <plan_id>
   ```

   **APPEND（增量追加）** → 从**本次新生成 plan** 的 `tables[]` 提取新表转成 append table-spec：`table_name` 取 `.` 后短名作 `table_name_short`，`layer/ddl_sql/etl_sql/source_tables` 原样直传（SQL 来自渲染器，**不手写、不改写**），`write_file` 写 `<workspace>/tmp/_append_spec.json`，再用**既有工作流的旧 plan_id** 执行追加：

   ```
   python <skills>/dw-warehouse-build/scripts/execute_pipeline.py --action append --plan-id <旧plan_id> --table-spec-file <workspace>/tmp/_append_spec.json
   ```

   ⚠️ **严禁对 APPEND 生成的新 plan_id 执行 `--action run`**：`run` 内部会先全局清理所有 `bld_` 前缀任务与工作流（跨 plan_id），已有全部表的编排会被删掉，只剩新 plan 里的表。

4. **调整或取消**：用户要求调整时，不执行旧 plan；先按反馈修改 spec/构建参数并重新生成 plan，然后重新完整输出新方案的 `confirm_question` 并再次等待确认。用户取消时立即结束，不执行任何写库操作。只有在不存在待确认方案、`plan_id` 不唯一，或当前回复包含调整/取消/拒绝语义时，才不得直接 handoff；此时按对应分支处理或先澄清。`intent.py` 仅用于初始建仓意图分类和 BUILD/APPEND 路径识别，不作为确认回复的硬门槛。

**禁止**：在方案正文输出前或未完整输出方案时调用 `ask_user`、handoff 或执行；只输出“创建 N 张表”等摘要；只说“现在向用户呈现方案”而不贴正文；把方案正文只放在工具结果或终端输出里；方案呈现后继续调用工具；把 L1 当作免确认自动执行。

**终端乱码处理（Windows 常见）**：bash 工具输出的中文可能被 GBK 解码显示乱码，这是显示层问题，不影响任何数据。`confirm_question` 的唯一真源是 **plan 文件**（UTF-8，`read_file` 读取始终干净）——终端输出乱码时直接 `read_file` plan 文件取 `confirm_question`，**禁止**依据脚本 stdout 重建方案正文、搜索脚本源码重建问句模板、反复读文件验证或因乱码中断流程。

- 本 skill 到 plan 落盘为止，**严禁**在本 skill 内执行建表/导入/ETL
- 执行期报 plan 格式问题：回到本 skill 修规格/plan 重新构建，不在执行端打补丁

## 硬性约束

| # | 约束 |
|---|---|
| C1 | 意图分类必跑 `intent.py`；规则命中即定路径。规则 UNKNOWN 时允许 LLM 语义预判，但必须经用户一句话确认后才能定路径 |
| C2 | L1 禁止生成任何 DWD/DWS/ADS 表；升级层级必须用户显式要求 |
| C3 | LLM 不手写 DDL/ETL——L2/L3 只交付语义规格 JSON，SQL 由渲染器生成 |
| C4 | 语义规格必须通过 `plan.py` 校验；校验失败修规格，不改脚本 |
| C5 | 执行前必须用户确认：先把 **plan 文件**中的 `confirm_question` 原样输出到当前 assistant 回复正文并结束本轮；用户下一轮明确确认后才 handoff `plan_id` 到 `dw-warehouse-build`；本 skill 不执行任何写库操作 |
| C6 | plan 是唯一交付物：格式与 `dw-warehouse-build` 的 execute_pipeline.py 完全兼容；格式问题回本 skill 修复，不在执行端打补丁 |
| C7 | 全程只读业务库；唯一写入是 plan/spec 文件 |
| C8 | 复用优先：discovery 标记 reuse 的表默认跳过，除非用户要求重建 |
| C9 | 指标诉求优先走指标驱动模式：声明 metrics[] 后 DWS/ADS 由代码推导；输出的指标覆盖清单必须呈现给用户核对 |
| C10 | 任何 tier（含 L1）都必须在执行确认前把完整方案正文（原样用 `confirm_question`）输出到 assistant 回复正文；只输出摘要、未输出方案就执行、或方案呈现后同轮继续执行，均视为违规 |
| C11 | APPEND 增量追加同样走本 skill 设计流程（spec → build → 只含新表的 plan → 确认门）；确认后从新 plan 的 `tables[]` 提取转 table-spec，用**旧 plan_id** 调 `dw-warehouse-build --action append`；**严禁**对 APPEND 新 plan_id 执行 `run`（会跨 plan_id 全局清理已有编排） |

## 边界

- 查询业务数据 → `nl2sql`
- 仅注册数据源直接分析（不建表不搬数据），或接入对象是数据库连接 → `dw-datasource-onboarding`（Excel 建仓已由本 skill 支持，仅注册不建仓也走 onboarding）
- 实时 CDC / 流式同步不在范围内
