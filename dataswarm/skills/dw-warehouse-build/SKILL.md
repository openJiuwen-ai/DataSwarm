---
name: dw-warehouse-build
type: execution
layer: L3
tags: [data-warehouse, data-engineering]
user-invocable: false
requires: [dw-data-engineering]
handoff_to: []
description: |
  数仓任务化执行。用户确认方案后执行建仓：
  读方案 → 建编排任务+工作流 → publish → 执行。
  通过 execute_pipeline.py 委托 plan_to_tasks.py 一次性完成建任务+工作流+节点+publish+血缘+执行。
  增量追加也走此技能。
allowed_tools: [bash, read_file, write_file]
---

# dw-warehouse-build

数仓**任务化执行**阶段。用户确认方案后，建编排任务+工作流并执行。

## 命令执行规则（必读）

运行环境可能是 Windows/Linux/macOS，**必须平台无关**：

- **每条 bash 调用只执行一个命令**，禁止 `&&`、`;`、`|`、`>` 等 shell 链接符
- **禁止写 bash 脚本**：不用 `for` 循环、`$()` 命令替换、管道、重定向等 shell 语法
- **禁止** `cd dir && command` — 用完整路径
- **只用单条 `python` / `python -m` 命令**调用现有 CLI 脚本，不要自己写 bash 脚本包装
- 传 JSON 参数用 `--table-spec-file`（先 write_file 写临时文件），不要用 `$(cat file)` 或 shell 转义

## When To Use

- 用户确认方案后要执行建仓
- 增量追加新表需要建任务+节点+执行
- debug-run / publish 任务

## When NOT TO Use

- 设计方案 → `dw-warehouse-design`
- 查询数据 → `nl2sql`
- 巡检 → `dw-inspection-orchestration`

## 执行流程

**核心原则：通过 `execute_pipeline.py` 一次性完成，不要分步手动调 CLI。**

`execute_pipeline.py` 内部委托 `plan_to_tasks.py`，一次完成：
建任务（DDL+数据任务）→ 建工作流 DAG → publish → 构建时血缘落库 → 同步 debug 执行 → 运行时血缘落库

### Step 0 — 复用设计阶段 plan_id（硬约束）

**本阶段不生成 plan，只读设计阶段已落盘的 plan_id**。

- 设计阶段 `dw-warehouse-design` 已用 `plan.py --action plan` 把 plan JSON 落盘到 `<workspace>/plans/<plan_id>.json`，并返回 `plan_id`
- **严禁**在本阶段重新生成 plan JSON、重新调 `plan.py --action plan`、或以"适配格式"为由重写 plan 文件
- 如果认为 plan JSON 格式有问题，**回到 `dw-warehouse-design` 修复**，不要在执行阶段打补丁

**判断 plan JSON 是否已是最终产物**：plan 顶层含 `name/business/biz_database/purpose/source_config/tables`，每张表含 `table_name/layer/ddl_sql/etl_sql/source_tables`。只要这些字段齐全，格式即正确，直接用 `plan_id` 执行。

**方言约束**：plan 的 `ddl_sql`/`etl_sql` 由设计阶段按 `dwcli datasource current` 返回的 dialect 生成（plan 顶层 `dialect` 字段）。`dialect=mysql` 时 DDL 应为 InnoDB 风格（无 `DUPLICATE KEY`/`DISTRIBUTED BY`）；`dialect=duckdb` 时 DDL 为双引号标识符（无 `DUPLICATE KEY`/`DISTRIBUTED BY`/列内 `COMMENT`），执行层对旧 Doris 风格 DDL 有兜底翻译；数据导入按方言自动分流（doris=Stream Load / mysql=批量 INSERT / sqlite=sqlite3 / duckdb=`CREATE OR REPLACE TABLE ... read_csv()/read_xlsx()` CTAS 物化到 workspace warehouse，天然幂等，csv 与 excel 源同链路）。若 plan 在执行期出现 `strftime`/`JULIANDAY`/`date(col,'-N days')` 且 `dialect=doris/mysql`，说明设计阶段漏读方言——回 `dw-warehouse-design` 重生 plan，不要在执行阶段打补丁。

### Step 1 — 读方案

```
python scripts/execute_pipeline.py --action read-plan --plan-id <plan_id>
```

### Step 2 — 全量执行（建任务+工作流+publish+执行）

```
python scripts/execute_pipeline.py --action run --plan-id <plan_id>
```

可选参数：
- `--no-run`：只建+publish，不执行（用于先检查任务/工作流定义）
- `--db <db_path>`：指定编排库路径（默认读环境变量 `JIUWENSWARM_ORCH_DB` 或 config `metadata_db`）
- `--workspace-id <id>`：多工作空间场景的工作空间 ID（读 plan/生成跳转链接用；缺省读环境变量 `JIUWENSWARM_WORKSPACE_ID`）

执行完成后输出 JSON，包含 `workflow_id`、`instance_id`、`status`、各层表清单，以及 `links`（数据管理平台跳转链接，Markdown 格式）。

**调度取自 plan**：建工作流时写入 `schedule_type=cron, schedule_cron=<plan.schedule_cron>`——由设计阶段按用户输入写入（缺省 `0 2 * * *`，每天 02:00 T+1）；执行阶段不改调度，用户要改调度回设计阶段重新出 plan。

**防重跑（硬性）**：`--action run` 对同一 `plan_id` 只执行一次；实例已成功（status=success）后严禁重跑，除非用户明确要求。

`links` 字段示例（成功执行后必有）：
```json
{
  "links": {
    "tables": "[📊 库表管理](/data-management/tables?db=olist)"
  }
}
```

**必须**在执行成功后把 `links.tables` 原样呈现给用户（Markdown 链接，Chat 界面可点击跳转）；**只呈现 `links.tables`，不要自行构造血缘管理链接**，也**禁止**给链接拼接 `http://localhost`、主机名、端口或协议——`links.tables` 是应用内相对路径，Chat 前端按当前访问域名自动解析，远程部署也能正确跳转。`biz_database` 取自方案。

### Step 3 — 验证结果（有界：最多 4 条命令，然后必须收尾）

用 `dwcli sql run` 查 Doris 验证，不臆造命令名/列名/文件名。**验证是有界动作，不是开放式排查**——按下述顺序执行，命中任一终止条件立即收尾汇报，禁止循环深挖。

**行数校验口径（硬性）**：`库内 COUNT(*) + 节点日志 filtered 行数 == plan 的 row_count` 即为导入完整。Stream Load 会拒绝个别脏行（空串进 NOT NULL 列、类型不可解析等），节点日志以 `[warn] <表>: N rows filtered` 记录；**差值 == filtered 属已知预期，不算异常**。

**命令模板**（只替换花括号占位；`<meta_ref>`/`<biz_ref>` 先跑 `python -m jiuwenswarm.cli.dwcli datasource current` 取 `metadata_db_ref`/`business_db_ref`，勿写死库名）：

```bash
# 1. 实例状态（1 次；dw_task_runs 真实列：instance_id/task_id/node_id/node_name/status/started_at/finished_at/stdout_path 等，无 task_name/layer 列）
python -m jiuwenswarm.cli.dwcli sql run --db-ref "<meta_ref>" --sql "SELECT status, COUNT(*) AS cnt FROM dw_task_runs WHERE instance_id = '{instance_id}' GROUP BY status"

# 2. 行数核对（每表 1 次；biz_db/表名/row_count 取自 plan）
python -m jiuwenswarm.cli.dwcli sql run --db-ref "<biz_ref>" --sql "SELECT COUNT(*) AS cnt FROM {biz_db}.{table}"

# 3. 仅当某表存在差值时：定位该表节点日志（1 次）
python -m jiuwenswarm.cli.dwcli sql run --db-ref "<meta_ref>" --sql "SELECT node_name, status, stdout_path FROM dw_task_runs WHERE instance_id = '{instance_id}' AND node_name LIKE '%{table_short}%'"
```

节点日志也可直接读：`~/.dataswarm/artifact/orchestration/runs/{instance_id}/node_node_imp_{table_short}.stdout.log`（read_file，UTF-8）。

**终止规则（硬性，三选一后必须结束回合）**：
- 全部表 `COUNT(*) == row_count` → 呈现 `links.tables` 与各表行数表，结束
- 某表差值 == 该表日志 filtered 数 → 汇报“导入完整，N 行被 Stream Load 过滤（脏数据，节点日志已记录）”，呈现 links，结束
- 差值无法用日志解释 → 汇报已知事实（表名/期望/实际/差值/日志摘要）后结束；深挖留给用户明确要求的后续任务

**禁止**：本地重算 CSV 行数（`import csv` 数行）；重复执行相同 SQL；为找指令重读 SKILL.md；读与差值无关的日志；任何超出上述边界的深挖。

**必读**：
- `dwcli` 只有 4 个子命令组：`databases / datasource / sql / plan`，没有 `db`、没有 `query`、没有 `exec`。**先 `python -m jiuwenswarm.cli.dwcli --help` 核对再下笔**。
- `--db-ref` 取值：一律从 `python -m jiuwenswarm.cli.dwcli datasource current` 取（元数据库查询用 `metadata_db_ref`，业务库查询用 `business_db_ref`），不传 `--db-ref` 时 dwcli 自动填元数据库 ref。**禁止**传 `doris://meta`、`doris://` 等遗留形态或 `default`、`bigdata`、`olist` 字面库名——遗留标记需未配置的 `doris.metadata_database` 项会 500，字面值后端 `db_connector.parse_db_ref` 不识别会报非法 db_ref。
- `dw_task_runs` 真实列见 `migrate_metadata_to_bigdata.py`：`run_id/instance_id/task_id/node_id/node_name/status/started_at/finished_at/sla_time/duration_s/exit_code/output_rows/retry_count/stdout_path/stderr_path/error_message/result_summary/start_ts/end_ts`。**没有 `task_name`、没有 `layer`**。如需 task 名，join `dw_tasks` 取 `name`；如需 layer，从 `dw_tasks.layer` 取。
- `dwcli sql run` 后端会自动给查询追加 `LIMIT`，所以**只发 SELECT**，不要发 `DESCRIBE`/`SHOW COLUMNS`/`EXPLAIN`（会被语法截断报错）。要查表结构用 `dwcli databases table --db <db> --table <table>`。
- 验证只读，不走 `sql ddl`；要 DDL 必须加 `--confirm`。

## 增量追加（Append）

追加新表到已有工作流：

```
python scripts/execute_pipeline.py --action append --plan-id <plan_id> --table-spec-file _append_spec.json
```

**用 `--table-spec-file`（先 write_file 写临时文件），不要用 `--table-spec` 传内联 JSON。**

**`--plan-id` 必须传既有工作流的 plan_id**（首次全量建仓的 plan_id，对应工作流 `bld_wf_<plan_id>` 已存在），**不是** `dw-warehouse-design` 本次追加新生成的 plan_id——append 用它读 `biz_database` 并定位要追加的目标工作流。

**与设计阶段的衔接（硬约束）**：table-spec 来自 `dw-warehouse-design` 为本次追加生成的新 plan——从其 `tables[]` 逐张提取：`table_name` 取 `.` 后短名作 `table_name_short`，`layer/ddl_sql/etl_sql/source_tables` 原样直传；SQL 由设计阶段渲染器生成，**禁止在执行阶段手写或改写 SQL**。

**⚠️ 严禁对追加生成的新 plan_id 执行 `--action run`**：`run` 会先跨 plan_id 全局清理所有 `bld_` 前缀的任务/工作流/血缘，已有表的编排会被删掉，只剩新 plan 里的表。

table-spec JSON 格式：
```json
[
    {
        "table_name_short": "dws_user_rfm_1d",
        "layer": "DWS",
        "ddl_sql": "CREATE TABLE IF NOT EXISTS `dws_user_rfm_1d` (...)",
        "etl_sql": "INSERT INTO `dws_user_rfm_1d` (...) SELECT ... FROM `dwd_order_detail` ...",
        "source_tables": ["olist.dwd_order_detail"]
    }
]
```

append 自动完成：
- 建 2 个任务（DDL + ETL）+ 2 个工作流节点 + 正确的 upstream 依赖
- 写 plan_table 记录
- publish → 构建时血缘落库
- 只执行新增节点（不重跑已有节点），运行时血缘自动落库
- **不删、不碰已有任务/节点/血缘**

## 原子能力参考（dwcli）

`plan_to_tasks.py` / `execute_pipeline.py` 内部经 dwcli 走 web API 完成建任务/工作流/节点，**本 skill 无需手动逐条调用**。如需单独操作单个任务/工作流（调试、运维），用 dwcli 原子命令（原子 skill 的本地 `*_cli.py` 已随 API 化撤销）：

| 能力 | dwcli 命令 | 对应原子 skill |
|------|-----------|---------------|
| 任务定义增删改查 | `dwcli task list\|get\|create\|update\|delete --id <task_id>` | dw-task-define |
| 工作流定义+节点 | `dwcli workflow create\|nodes --id <wf_id> --json-file nodes.json` | dw-workflow-define |
| 生命周期状态转换 | `dwcli task\|workflow publish/unpublish --id <id>`、`dwcli instance approve/resume --id <id>` | dw-task-schedule |
| 执行/实例/日志 | `dwcli task\|workflow debug --id <id>`、`dwcli instance wait\|task-runs\|logs --id <id>` | dw-task-execute |

**注意**：分步手动调会遗漏血缘写入（`plan_to_tasks.py` 会在 publish 时经 API 统一触发），且经 `--code` 传 SQL 易因 shell 转义出错。执行建仓必须走 `execute_pipeline.py`。完整参数见各原子 skill 的 SKILL.md 或 `dwcli <group> <action> --help`。

## 硬性约束

| # | 约束 |
|---|------|
| B1 | 执行前必须用户确认（confirm 门控） |
| B2 | 分层序由工作流 DAG 保证：ODS→DWD→DWS→ADS |
| B3 | 血缘随任务 publish（构建时）+ debug 执行（运行时）自动落库 |
| B4 | 管理元数据在 bigdata，业务数据在业务库，不混库 |
| B5 | 禁止写中间脚本，直接调 `execute_pipeline.py` |
| B6 | 禁止绕过任务化直接写 Doris |
| B7 | 禁止重新生成 / 重新落盘 plan。本阶段只读设计阶段返回的 `plan_id`，plan JSON 格式问题回 `dw-warehouse-design` 修复 |
| B8 | 探查/查表一律走 dwcli（`dwcli databases list\|tables --db <db>`、`dwcli databases table --db <db> --table <t>`、`dwcli sql run --db-ref <ref> --sql "<sql>"`），原子 skill 的 `*_cli.py` 已随 API 化撤销、`dw-db-connector` 退化为文档索引，不再有本地 CLI。不要凭记忆拼参数，先 `dwcli <group> <action> --help` 核对 |
| B9 | 验证查询走 `dwcli sql run --db-ref <ref> --sql "<sql>"`，子命令组只有 `catalog/datasource/sql/plan`。**禁止**幻觉 `dwcli db query` / `dwcli exec` / `dwcli query` 等不存在的子命令；不确定就先 `python -m jiuwenswarm.cli.dwcli --help` 核对。`--db-ref` 从 `dwcli datasource current` 取（业务库 `business_db_ref` / 元数据库 `metadata_db_ref`），**禁止**传 `doris://`、`doris://meta` 遗留形态或 `default`/`bigdata`/`olist` 字面值。`dw_task_runs` 真实列不含 `task_name`/`layer`，要这两个字段 join `dw_tasks` 取 |
| B10 | append 的 `--plan-id` 必须是既有工作流的 plan_id；table-spec 从设计阶段新 plan 的 `tables[]` 提取（SQL 由渲染器生成，禁止执行端手写/改写）。**严禁**对追加生成的新 plan_id 执行 `run`（会跨 plan_id 全局清理已有编排） |
