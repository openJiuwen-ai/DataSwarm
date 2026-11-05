---
name: nl2sql
description: |
  自然语言转 SQL 的取数能力，只承接**纯取数**请求（用户只要数据或 SQL，不要分析结论）。
  输入一句问话，在当前工作空间绑定的业务库上走三段：备料（召回表 + 定表 + 采维值）→
  写 SQL → 跑 SQL，交回 SQL 文本与结果（导出 CSV 文件 + 头部若干行）。所有元数据与
  数据访问都经 dw-db-connector 走 web API，脚本不直连数据库。维值在备料阶段只对
  选定表做轻量采样；执行结果为空时再按 SQL 里的过滤列补采核对。
  **排他边界**：问句含任何分析意图（分析/对比/统计/分布/占比/排名/趋势/报告/
  归因/为什么）时**禁止用本 skill**——一律走 dw-analysis（其内部已含完整取数
  与口径账本，绕行会丢失 G 等级/检查/manifest）。不做降级链路（无语义层 /
  无指标模型 / 无 fallback_context）。
---

# nl2sql —— 纯取数

## 何时转交 dw-analysis

nl2sql 只承接**纯取数**。下列请求一律交给 `dw-analysis`，不要借本 skill 绕开四层链路账本：

- 要分析性产出：报告 / 趋势解读 / 归因解释 / 多成分拆解
- 涉及未发布口径（指标体系未覆盖的维度/口径，需探索或澄清）
- 已发布指标能直接回答（→ dw-analysis 的 metric 直查路径）

> **数据访问**：全部经 `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现，
> `DWAGENT_BASE` 可覆盖）调 web API。**web 服务须常驻**；连不上时 dwcli 以退出码 2 报连接失败。

## 入参

- `query`（必填）：用户原话
- `catalog` / `schema`（可选）：只影响选表时的偏好，**不改变执行库**。执行库固定取当前工作空间绑定的业务库，
  解析顺序：显式 `--workspace-id` > env `JIUWENSWARM_WORKSPACE_ID`（bash 子进程已注入）；多工作空间又缺上下文时直接报错，
  不会回退到任何默认库

## 连接从哪来

nl2sql 本身不带连接配置，沿用 `dw-db-connector` 的约定：

- 工作空间：`~/.dataswarm/config/datasource.yaml` 的 `workspaces:` 段（YAML 空间），或界面创建后经数据源接入绑定业务库的空间（dw-datasource-onboarding，一个工作空间一个数据源）
- 业务库 ref：`_db_client.py` 按上面的工作空间解析顺序取得
- db-ref 格式与环境变量回退：见 `dw-db-connector/SKILL.md`

调用前不需要 `source` 任何 env 文件。缺工作空间上下文报 `WORKSPACE_CONTEXT_REQUIRED`，空间没绑业务库报 `MISSING_CONFIG`。

查看当前空间绑定的业务库：
```bash
python -m jiuwenswarm.cli.dwcli datasource current   # 输出 business_db_ref（会话已注入 workspace 时）
```

## 输出约定（所有脚本通用）

- 成功：stdout 一个 JSON 对象，退出码 0
- 失败：stderr 一个 JSON 对象，退出码 1，形如
  `{"ok": false, "error": {"kind": "...", "why": "...", "hint": "...", "retry": false, "stage": "..."}}`
  - `kind`：PERMISSION_DENIED / TABLE_NOT_FOUND / AMBIGUOUS_TABLE / COLUMN_NOT_FOUND / SYNTAX_ERROR / TIMEOUT / UNKNOWN / MISSING_CONFIG 等
    （`AMBIGUOUS_TABLE`：裸表名在多个 schema 下同名，按 `why` 里列出的候选改用全限定名重试）
  - `why` 原始报错，`hint` 建议的修法，`retry` 为 true 表示改 SQL 后值得再试
  - `stage` 只有 SQL 类脚本才带：`SQL_EXPLAIN`（预检阶段）/ `SQL_EXECUTION`（正式执行阶段）

## 第一段：备料（召回表 → 定表 → 采维值）

这一段做完，手里应有**目标表、列结构、过滤列的真实取值**，第二段直接写 SQL。

### ①-1 混合召回候选表（附带列结构）

```bash
python scripts/recall_tables.py --query "最近6个月各品类销售趋势" --top_k 10
```

输出 `items` 按 `score` 降序，每项已带 `fields`（列名/类型/注释），`count` 为条数。`matched_on` 说明这张表从哪条通道进来：

- `lexical:name` / `lexical:comment`：表名或注释字面命中
- `emb`：服务端 schema 向量召回（字面不像、语义相近的表靠它进入候选）
- `glossary`：经术语库桥接命中（如「成交规模」经术语「商品交易总额(GMV)」扩出 GMV 后命中）

顶层 `glossary_terms` 列出本次用上的业务术语；`warnings` 记录没能参与的增强通道
（如 `emb_unavailable` / `vector_recall_skipped:duckdb_source`）。**有 warnings 照常往下走**：
增强通道缺席时结果只是退回纯字面匹配，不代表失败。

### ①-2 定表（模型推理）

- `score` 为字面/向量混合分（0~1），高分优先；分数持平时，`matched_on` 带 `emb` 的通常更贴题
- `row_count` 取自 `information_schema.tables.table_rows`，只是估算——别拿它推断「表小可以全表扫」
- 表注释与问话含义吻合的优先；列注释写得清楚的优先
- **顺手圈出要采样的列**：从选定表的 `fields` 里挑 3~5 个与问话过滤条件最相关的维度列（字符/分类列），交给 ①-3

### ①-3 采样选定表的维值（轻量）

只对 ①-2 选中的表采样，目的是让第二段写进 `WHERE` 的值确实存在：

```bash
python scripts/sample_dim_values.py --table dws_monthly_sales --columns "year_month,product_category,order_status"
```

输出 `dim_columns[*]`（`name`/`type`/`comment`/`values`）与 `count`；`mode` 标明取值方式：

- 不超过 3 列：`distinct` 方式，一次去重查询
- 超过 3 列：`sample` 方式，取 1000 行在内存里去重
- 时间列（`date`/`datetime`）与数值列（`int`/`double`/`decimal`）自动略过
- 某列没采到取值，只说明样本里没拿到，列本身照常可用——写 SQL 时在该过滤条件旁加行内注释「取值未采到」
- 只采选定表（一般 1~2 张），不扫全部候选，控制查询开销

### ①-4 补拉完整列结构（通常不需要）

只在这几种情况下用：

1. ①-1 返回的 `fields` 为空或明显不全
2. 需要 `ordinal`、分区等召回结果里没有的元信息
3. 已知确切表名但没被召回

```bash
python scripts/get_schema.py --table dws_monthly_sales [--include_partitions]
```

## 第二段：写 SQL（模型推理）

这一段不调脚本。**先看 ①-1 输出顶层的 `source_type`**：`doris`/`mysql` 按「SQL 方言注意事项（MySQL 协议族）」写，
`duckdb` 按「SQL 方言注意事项（DuckDB 文件源）」写——两套写法不通用，Doris 习惯的 SQL 拿到 DuckDB 上会直接报错。

过滤条件里的值（`WHERE`/`IN`）优先用 ①-3 采到的真实取值；没采到取值的列按 ①-3 的注释办法处理，不要自己编值。

## 第三段：跑 SQL（预检 → 关联表探空 → 执行 → 空结果复核）

### ③-1 EXPLAIN 预检

```bash
python scripts/explain_sql.py --sql "SELECT ..."
```

成功输出 `{"ok": true, "plan": "...", "estimated_rows": "unknown"}`；`plan` 为执行计划文本。

### ③-2 关联表探空（只在多表 JOIN 时）

SQL 用到多张表时，正式执行前先确认参与关联的表都不是空表：

```bash
python scripts/check_table_rows.py --tables "bridge_a,bridge_b,dim_product"
```

- 全部有数据：`{"ok": true, "tables": [{"table": ..., "row_count": ...}]}`
- 遇到空表即停：`{"ok": false, "empty_table": "<表名>"}`——由模型决定改成单表查询还是终止
- 单表 SQL 跳过这一步

### ③-3 正式执行

```bash
python scripts/execute_sql.py --sql "SELECT ... LIMIT 500"
```

成功输出：

```json
{"ok": true, "sql": "...", "row_count": 123, "columns": [...],
 "export": {"file": "<CSV 文件路径>", "head": [["v1", "v2"], ...]}}
```

`export.file` 是完整结果的 CSV 文件，`export.head` 为前 10 行。是否继续分析、出图或交付文件由模型判断。

### ③-4 空结果复核（按过滤列补采维值）

正式执行**结果为空**（`row_count = 0`），或报错指向过滤值（值不存在/类型不符）时进行：

1. 对 SQL 里 `WHERE`/`IN` 用到的维度列再采一次取值（命令同 ①-3，可多给几列）
2. 拿 SQL 里写的值和实际取值对照：
   - 有值不在实际取值中 → 换成真实值，回第二段改写后再执行（**只重来 1 次**，避免打转）
   - 写的值都存在 → 空结果就是业务现状，如实回复「没有数据」，不再重试

> 这一步只为兜住 ①-3 轻量采样漏掉的列或取值变化；大多数请求在 ③-3 一次跑完。

## 使用守则

1. **一个脚本一件事**：脚本之间不互相调用，组合由模型按上面三段编排
2. **直接跑脚本**：一律 `python scripts/<name>.py`，不套 wrapper
3. **找表只走 `recall_tables.py`**（底层查 `information_schema`）：不要用 `SHOW TABLES` / `DESCRIBE` 自行摸索
4. **脚本不读交互输入**：所有参数走命令行
5. **没有降级路径**：不提供 fallback_context，失败即如实报告
6. **只做被要求的查询**：脚本不会顺带多跑别的 SQL
7. **连接统一走 `_db_client.py`**（委托 `dw-db-connector`）：不要自己 `pymysql.connect`；业务库按工作空间解析，bash 子进程已注入 env，不要手动传 workspace 参数去覆盖

## SQL 方言注意事项（MySQL 协议族：doris / mysql）

`source_type` 为 `doris` 或 `mysql` 时适用。目标库兼容 MySQL 协议但有方言差异。生成 SQL 时注意：

### 兼容 MySQL 但有差异的函数

| 函数 | 目标库 | MySQL | 备注 |
|---|---|---|---|
| `DATE_FORMAT` | ✅ | ✅ | 兼容 |
| `STR_TO_DATE` | ✅ | ✅ | 兼容 |
| `DATEDIFF` | ✅ | ✅ | 一致 |
| `TIMESTAMPDIFF` | ✅ | ✅ | 一致 |
| `percentile_approx` | ❌ | ❌ | 目标库用 `APPROX_COUNT_DISTINCT` 或 `PERCENTILE` |
| `LAG` / `LEAD` | ✅ | ✅ | 兼容 |
| `CTE` / `WITH` | ✅ | ✅ MySQL 8.0+ | 兼容 |

### 目标库特有 / 推荐使用

- `BITMAP_UNION` / `BITMAP_COUNT`：UV 类查询
- `LATERAL VIEW` + `explode`：数组展开
- `COLLECT_SET` / `COLLECT_LIST`：聚合收集

### 禁用 / 注意

- 不用 `LIMIT` 不加 `WHERE`：大表全扫代价高
- 不用 `SELECT *`：列存只读所需列更快
- 不用 `PERCENTILE_APPROX`（目标库函数名是 `PERCENTILE` 或 `APPROX_PERCENTILE`）
- 时间列分桶：用 `DATE_FORMAT(col, '%Y-%m')` 或 `date_trunc(col, 'month')`，不要套 `spark_safe_*` 宏（那是 Hive/Spark 体系）
- **别名/列名禁止用目标库保留字**：`ROWS`、`ORDER`、`GROUP`、`YEAR`、`MONTH`、`LAG` 这类在窗口函数/分区语境里是关键字，直接当裸别名会 `mismatched input`。用带下划线的别名替换（`COUNT(*) AS NORMAL` 也危险——用 `row_cnt`/`order_cnt`/`month_no` 这类）。报错特征是 Doris 1105 `errCode = 2, mismatched input '<token>' expecting {<EOF>, ';'}`
- **空结果先做 ③-4 复核再下结论**：`row_count = 0` 不一定是没数据，可能是过滤值写错——对照维值后再答复

## SQL 方言注意事项（DuckDB 文件源）

`source_type` 为 `duckdb`（csv 目录/单文件/glob/excel 源）时适用。**Doris 节的函数表在这一节全部失效**，不要按 MySQL 协议族习惯生成。

### 与 Doris 习惯的关键差异

| 主题 | DuckDB 正确写法 | Doris 习惯（此处禁用） |
|---|---|---|
| 时间格式化 | `strftime(col, '%Y-%m')` | `DATE_FORMAT(col, '%Y-%m')` |
| 时间截断 | `date_trunc('month', col)` ——**参数序相反，粒度在前** | `date_trunc(col, 'month')` |
| 数组展开 | `CROSS JOIN UNNEST(arr) AS t(x)` | `LATERAL VIEW` + explode |
| 近似/去重计数 | `COUNT(DISTINCT x)` | `APPROX_COUNT_DISTINCT` / `BITMAP_UNION` |
| 聚合收集 | `list(x)` / `list(DISTINCT x)` | `COLLECT_LIST` / `COLLECT_SET` |
| 近似分位数 | `approx_quantile(x, 0.95)` | `PERCENTILE` / `APPROX_PERCENTILE` |
| 标识符引号 | 双引号 `"col"`（反引号也兼容，但与 DESCRIBE 输出一致优先双引号） | 反引号 `` `col` `` |
| 日期差 | `date_diff('day', a, b)` ——**间隔单位在前，且参数序与 MySQL DATEDIFF 相反** | `DATEDIFF(a, b)` / `DATEDIFF(b, a)` |

### DuckDB 可用特性

- 窗口函数 / CTE / `WITH` / `LAG` / `LEAD`：兼容
- `CAST(x AS DOUBLE)` / `TRY_CAST`：可用（`TRY_CAST` 失败返回 NULL 而非报错，脏数据友好）
- 正则：`regexp_matches(col, 'pattern')`、`regexp_extract(col, 'pattern', i)`

### 禁用 / 注意

- 不用 `LIMIT` 不加 `WHERE`：文件源全量扫描代价高
- 不用 `SELECT *`：只读所需列
- csv 目录源每文件一个视图，**视图无主键/索引概念**，JOIN 键选重复率低的列
- 表/列不存在、函数不支持时报错特征是 `Catalog Error: Table with name ... does not exist` / `ParserException: Syntax error` / `Binder Error`——见到这类报错说明写的是 DuckDB 不认识的语法，按上表改写，不是重试可解
- 文件源列类型由 `read_csv` 嗅探决定，日期列可能是 VARCHAR：比较前用 `TRY_CAST` / `strptime` 显式转型，不要直接与日期字面量比较

### 与指标语义层的协作（探索口径）

- `dw-metrics ask` 返回 `route=exploratory_no_metric` 时，其 `schema_subgraph_path` 里的子图（表/列/join/血缘）是**本技能写 SQL 的优先上下文**：表列范围与 join 键以子图为准，子图外的表不用。
- 需要服务端代跑探索 SQL 时调 `dwcli metric-v2 explore --json-file -`（body `{"question": ..., "subgraph_scope": <子图 JSON 可选>}`）；返回 `authoritative=false`，答复用户时标明探索口径。
- 子图里 `verified=false` 的 join 边只作线索，写进 SQL 前用 `PRAGMA`/样本核对键基数。
