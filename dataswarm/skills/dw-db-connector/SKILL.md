---
name: dw-db-connector
description: 数据库连接与探查能力已上收服务端。当用户提到"探查数据库/看库里有哪些表/表结构/查看表定义/数据库结构/inspect schema/看看表/列信息/索引/连库/查表"时使用。本 skill 仅作文档索引——查询/探查/写库一律经 `dwcli sql|databases`(web API),skill 侧不再持有连接代码。连接层实现在 `jiuwenswarm.common.db_connector`(服务端)。
allowed_tools: [bash, read_file]
---

# dw-db-connector · 数据库连接能力

> 本 skill 的脚本已撤销（方案 `docs/skill-cli-web-api-plan.md` §7.1）。
> 连接层 `db_connector.py` 搬至 `jiuwenswarm/common/db_connector.py`，服务端 7 处 sys.path hack 改为正规 import；skill 侧不再持有任何连接代码。

## 现在怎么连库

**所有 DB 访问经 `dwcli` 走 web API**（`/api/dataagent/*`），skill 不自己连接数据库：

```bash
# 只读查询（SELECT/SHOW/DESCRIBE/PRAGMA/EXPLAIN）
dwcli sql run --db-ref <ref> --sql "SELECT ..."            # 返回 {ok, rows, columns, truncated, row_count, elapsed_ms, db_ref}
dwcli sql explain --db-ref <ref> --sql "SELECT ..."        # EXPLAIN 专用路径（不拼 LIMIT）
dwcli sql ddl --db-ref <ref> --sql "CREATE..." --confirm    # 写操作需 confirm

# 表/Schema 探查
# --db 可省略：--db-ref 指针自带库名时（如 doris://<库名>）自动推导
dwcli databases tables --db-ref doris://<db>                # 表清单（推荐：只传 ref 即可）
#   row_count 为 null 表示「未统计」（sqlite 等无 information_schema 估算的库不做全表扫描），
#   不是「空表」——要真实行数用下一行的表详情
dwcli databases table --db-ref doris://<db> --table <name>  # 表详情（DDL+columns+pk+indexes+row_count 真实行数）
# schema-snapshot 是 json_body 路由，body 顶层含 db_ref（可选）/tables/limit，须经 --json-file 传
dwcli databases schema-snapshot --json-file body.json       # 批量全表快照（G18），body 例：{"db_ref": "<ref>", "limit": 200}
```

> 简写兼容：`dwcli sql "<SQL>"` / `dwcli sql run "<SQL>"`（把 SQL 当位置参数）也会被自动重写为 `sql run --sql "<SQL>"`；但**推荐始终用上面 `--sql` 的正规写法**。

> **`--db-ref` 缺省语义**：`sql run`/`sql explain`/`sql ddl`/`databases` 组不传 `--db-ref` 时，dwcli **客户端**自动填入**当前工作空间业务库 ref**（workspace 解析链取 `business_db_ref`；空间未绑源/多空间缺上下文时大声失败，不静默落库）。**不是**系统元数据库——查元数据库表（dw_tasks/dw_findings 等）须显式传元库 ref（从 `dwcli datasource meta` 的 `meta_db_ref` 取）。

### 多行/含引号 SQL 的传法（建表、DDL、写操作必读）

**禁止用 shell 引号内联多行 SQL**：Windows 下 cmd 会把跨行参数拆散，报 `dwcli: error: unrecognized arguments`。统一走「写文件 + `--sql-file`」：

```bash
# 1) 把 SQL 写到文件（heredoc 首命令 cat 走 bash，正常执行；<<'SQL' 带引号分隔符不展开内容）
cat > create_test_table.sql <<'SQL'
CREATE TABLE `test_demo_table` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NULL COMMENT '名称'
) ENGINE=OLAP
DUPLICATE KEY(`id`)
DISTRIBUTED BY HASH(`id`) BUCKETS 10
SQL

# 2) 用 --sql-file 从文件读原文（不再经 shell 引号）；文件用绝对路径（bash cwd 可能不是写文件的目录）
python -m jiuwenswarm.cli.dwcli sql ddl --db-ref doris://<业务库名> \
    --sql-file /full/path/to/create_test_table.sql --confirm
```

`--sql-file` 同样适用于 `sql run`/`sql explain`（含换行/反引号的查询也推荐文件传）。单行 SQL 仍可直接 `--sql "..."`。

> **Doris 方言提醒**：建表 SQL 的 `COMMENT 'xx'` 必须**单引号**字符串字面量，双引号会报 `mismatched input expecting STRING_LITERAL`。注意 `SHOW CREATE TABLE` 输出里显示的是双引号（`COMMENT "xx"`），那是 Doris 的展示格式，**不能照抄回 CREATE TABLE**——回写时改成单引号。

> **写操作必须用 `sql ddl --confirm`**：建表/DDL/DML（CREATE/ALTER/INSERT/UPDATE/DELETE）一律走 `dwcli sql ddl --db-ref <ref> --sql "..." --confirm`。**禁止用 `sql run` 执行写操作**——sql run 是只读查询通道，内部对 SQL 有解析适配，写操作走它会报 `no viable alternative at input 'CREATE ...'` 等解析错误。

## 探查场景速查（被路由到本 skill 时直接用这些命令）

**不写 Python 连库代码**，全部用 `dwcli` bash 命令：

| 探查意图 | 命令 |
|---|---|
| 看库里有哪些表 | `dwcli databases tables --db-ref doris://<db>`（`--db` 可省，ref 自带库名时自动推导） |
| 看某张表结构（列/类型/PK/索引/真实行数） | `dwcli databases table --db-ref doris://<db> --table <name>`（返回 `row_count`；表清单里为 `null` 时用它确认是否有数据，**不要**把 null 当空表） |
| 列出所有可连数据库 | `dwcli databases list` |
| 表数据预览 | `dwcli databases preview --db <db> --table <name> --limit 10` |
| 批量全表快照（多表 schema + 预览） | `dwcli databases schema-snapshot --json-file <body.json>` |
| 任意只读 SQL（SELECT/SHOW/DESCRIBE/PRAGMA） | `dwcli sql run --db-ref <ref> --sql "..."` |
| EXPLAIN 执行计划（不拼 LIMIT） | `dwcli sql explain --db-ref <ref> --sql "..."` |
| DDL/写操作（需 confirm） | `dwcli sql ddl --db-ref <ref> --sql "CREATE..." --confirm` |

### 数据工作空间（必读）

**禁止手动传 workspace 参数覆盖**：bash 子进程已自动注入 `JIUWENSWARM_WORKSPACE_ID`（= 用户在界面选中的数据工作空间），dwcli 会自动读取。任何手动传 `--workspace` / `--workspace-id` 的行为都会覆盖用户选择，属于错误操作。

**禁止凭记忆/凭本文档示例写死库名**：库名随部署环境变化，本文档示例中的 `doris://<db>` 均为占位符。实际业务库/元数据库 ref 以命令查询结果为准：

```bash
dwcli datasource current    # 返回当前工作空间的 workspace_id/business_db_ref/metadata_db_ref
```

用查询返回的 `business_db_ref`（查业务表）或 `metadata_db_ref`（查 dw_* 元数据表）作为 `--db-ref` 取值。

## db-ref 取值

- 实际 ref 从 `dwcli datasource current` 查询获得（见上文「数据工作空间（必读）」），勿写死库名
- `doris://<业务库名>` — 业务库（对应 workspace 的 `business_db_ref`）
- `doris://<元数据库名>` — 系统元数据库（对应 workspace 的 `metadata_db_ref`，存 dw_* 表）
- `sqlite://path/to/x.db` — SQLite
- 不传 `--db-ref` — dwcli 客户端自动填**系统元数据库 ref**（`metadata_db`），非业务库；查业务表须显式传业务库 ref

### 常用元数据查询

```bash
# <meta_ref> 先经 dwcli datasource current 查询获得（示例用占位符，勿写死库名）
# 查已有建仓 plan
dwcli sql run --db-ref <meta_ref> \
    --sql "SELECT plan_id, biz_database, status FROM plan ORDER BY created_at DESC LIMIT 5"

# 查任务执行状态（替换 <instance_id>）
dwcli sql run --db-ref <meta_ref> \
    --sql "SELECT status, COUNT(*) FROM dw_task_runs WHERE instance_id='<id>' GROUP BY status"

# 查血缘边
dwcli sql run --db-ref <meta_ref> \
    --sql "SELECT source_db, source_table, target_db, target_table FROM table_lineage WHERE task_id LIKE 'bld_%' LIMIT 20"
```

## db-ref 格式（仍沿用，由服务端解析）

```
sqlite://path/to/x.db        SQLite（或裸路径）
doris://host:9030/database    Doris（或 doris:// 用 DORIS_* 环境变量补全）
doris://<元数据库名>          Doris 系统元数据库（= workspace 的 metadata_db_ref，指针自带库名）
mysql://host:3306/database    MySQL（或 mysql:// 用 MYSQL_* 环境变量补全）
```

元数据库将切换为 MySQL 系关系型库（业务库才用 Doris）——新代码方言无关，见方案 §7.5。

## 给其他 skill 的复用约定

- skill 侧**禁止** `import sqlite3` / `import pymysql` / `import db_connector` / 自建 HTTP 客户端
- 一切 DB 访问经 `dwcli` 子进程；SQL 文本处理（纯函数）用 `_dw_common/scripts/dw_sql_utils.py`
- 连接池/只读护栏/占位符适配/`_translate_sql` 等逻辑全部在服务端 `jiuwenswarm.common.db_connector`，skill 不再碰

## 工作空间数据源配置

业务库 = **当前工作空间绑定的数据源**（YAML 空间声明在 `workspaces:` 段；界面空间经数据源接入绑定，单空间单源）。`~/.dataswarm/config/datasource.yaml`（服务端读取，skill 侧不需要；库名随环境变化，实际以 `dwcli datasource current` 查询为准）：
```yaml
workspaces:
  analytics:                        # 工作空间 id
    name: 分析空间
    default_db: doris://<业务库名>   # 业务库（指针自带库名）
    metadata_db: sqlite://<元库路径> # 系统元数据库（每个工作空间一个）
doris:
  host: ...                       # 连接凭据段：workspaces 里的 doris:// ref 共用
```

db-ref 指针形态：**`doris://dbname`（标准形态，库名直接可见，凭据仍走 doris: 段）**｜`doris://`（遗留兼容，库名走 DORIS_DATABASE 环境变量）｜`doris://meta`（遗留兼容标记，需 config `doris.metadata_database` 配置项——**新代码一律用 `doris://dbname` 指针形态，勿写 `doris://meta`**）。db_identity 指纹对元数据库自动加 `[:meta]` 后缀——按库名判定（与 config 元数据库同名即加），历史 findings 指纹不失配。
