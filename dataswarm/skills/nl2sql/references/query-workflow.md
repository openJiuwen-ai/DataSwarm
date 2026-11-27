# 取数操作手册

## 调用约定

`skill_directory` 来自 `skill_tool(skill_name="nl2sql")`；用脚本绝对路径调用，命令工具显式 `shell_type: bash`。SQL 脚本接收 `--sql`，不支持 `--sql-file`；复杂 SQL 先保存文件，用 `--sql "$(cat '/absolute/query.sql')"` 读取。保留完整查询 JSON 后再解析预览，不用 head/tail 截断 JSON。

需要确认当前数据源时执行 `python -m jiuwenswarm.cli.dwcli datasource current`。缺上下文返回 `WORKSPACE_CONTEXT_REQUIRED`，未绑源返回 `MISSING_CONFIG`；配置排查见 dw-db-connector，不能改空间或连接绕过错误。

## 第一段：备料（召回表 → 定表 → 采维值）

这一段确认目标表、列结构和过滤条件依据。已有用户明确值、已确认映射或足够的观测值时，不重复采样；仅补当前查询的未决条件。

### ①-1 混合召回候选表（附带列结构）

```bash
python "{skill_directory}/scripts/recall_tables.py" --query "列出上月各品类销售额" --top_k 10
```

输出 `items` 按 `score` 降序，每项已带 `fields`（列名/类型/注释），`count` 为条数。`matched_on` 说明这张表从哪条通道进来：

- `lexical:name` / `lexical:comment`：表名或注释字面命中
- `emb`：服务端 schema 向量召回（字面不像、语义相近的表靠它进入候选）
- `glossary`：经术语库桥接命中（如「成交规模」经术语「商品交易总额(GMV)」扩出 GMV 后命中）
- `value`：问话里出现了这张表某列的真实取值（来自语义采集的取值字典），表名、列名都不像也能进来

顶层 `metric_discovery.candidates` 是当前源范围内的有界发布指标候选，含版本与定义。`channel_status=unavailable` 表示发现通道不可用，不等于没有指标；`metric_source_not_bound` 表示当前源未对应注册源，`metric_source_map_unavailable` 表示注册读取失败。

顶层 `glossary_terms` 列出本次用上的业务术语；`warnings` 记录没能参与的增强通道
（如 `emb_unavailable`）。通道缺席不等于查询失败；保留实际口径与限制。
`catalog_complete=false` 表示部分表结构读取失败；`schema_errors` 列出失败表，当前结果只覆盖其余可读表，不据此断言全目录没有其他相关数据。结构主通道整体失败或所有表均失败时返回结构化错误，不能当作空命中。

顶层`field_context`包含`source_id/source_type`、`semantic_versions.snapshots`及`packs.schema_tables[*].columns[*].field_context`。字段有`status`、快照、`profile`和最多10个`observed_values`，最多补充30列；`missing`及warnings为缺项信息，不触发补采画像。

顶层 `value_matches` 列出问话里认出的取值及其所在表列（`value`/`table`/`column`/`match_type`）。
`ambiguous_values` 非空表示**同一个取值同时出现在多列**（例如某个名字既是城市又是州），
按技能正文的歧义原则选择或澄清。

### ①-2 定表（模型推理）

- `score` 与 `matched_on` 是候选发现信号，按定义、范围、粒度与列结构判断业务适配，不因某个召回通道优先采用。
- `row_count` 未知时为占位值，不据此推断表为空或可以全表扫描；结构读取不统计业务行数。
- 表注释与问话含义吻合的优先；列注释写得清楚的优先
- 从选定表的 `fields` 里识别过滤值或编码尚未明确的列，必要时交给 ①-3；不设置采样列数量配额。

### ①-3 采样选定表的维值（轻量）

仅在过滤值或编码需要补证时，对选定表的相关列采样；有界样本提供观测依据，不证明未命中的值不存在：

```bash
python "{skill_directory}/scripts/sample_dim_values.py" --table dws_monthly_sales --columns "year_month,product_category,order_status"
```

输出 `dim_columns[*]`（`name`/`type`/`comment`/`values`）与 `count`；`mode` 标明取值方式：

- 对显式选择的列做有界去重，保留实际列类型；只选择当前过滤需要的列，不要求列注释包含预设关键词。
- 结果范围见 `data_scope`；未观测到的值不代表源中不存在。
- 只采当前查询需要的选定表与列，不扫全部候选。

### ①-4 补拉完整列结构（通常不需要）

只在这几种情况下用：

1. ①-1 返回的 `fields` 为空或明显不全
2. 需要 `ordinal`、分区等召回结果里没有的元信息
3. 已知确切表名但没被召回

```bash
python "{skill_directory}/scripts/get_schema.py" --table dws_monthly_sales [--include_partitions]
```

## 第二段：写 SQL（模型推理）

以 `execution_environment.sql_dialect` 写 SQL，表名使用目录规范标识符。`source_type` 与执行引擎可能不同，例如 Redis 物化后用 DuckDB。DDL 只生成时用 `target_sql_dialect`；未指定目标方言则先明确。日期、类型转换、引号与限行语法须匹配目标方言。

## 第三段：跑 SQL（预检 → 执行 → 按疑点复核）

### ③-1 EXPLAIN 预检

```bash
python "{skill_directory}/scripts/explain_sql.py" --sql "SELECT ..."
```

结果 `status=CHECKED` 表示已验证计划；`SKIPPED` 表示该源无可用计划检查，保留 `reason` 后可继续受控查询，不能宣称计划验证成功。仅生成 SQL 时加 `--static-only`，返回 `STATIC_ONLY`，不执行数据库查询；DDL 源按此模式交付。

### ③-2 关联表探查（按需）

按实际实体关系、粒度与关联语义选键；怀疑参与表为空且该信息会影响判断时，可调用以下检查。它会逐表计数，考虑扫描成本，不作为所有多表查询的必经步骤：

```bash
python "{skill_directory}/scripts/check_table_rows.py" --tables "bridge_a,bridge_b,dim_product"
```

- 全部有数据：`{"ok": true, "tables": [{"table": ..., "row_count": ...}]}`
- 遇到空表时脚本停止后续计数：`{"ok": false, "empty_table": "<表名>"}`；它不是原查询无效的判定。LEFT JOIN 右表为空或反连接查询仍可能返回正确结果，不因此删除关联或改写用户问题。
- 无具体疑点时直接执行原查询；非空检查也不证明关联键、基数或口径正确。

### ③-3 正式执行

```bash
python "{skill_directory}/scripts/execute_sql.py" --sql "SELECT ... LIMIT 500"
```

成功输出：

```json
{"ok": true, "sql": "...", "row_count": 123, "columns": [...],
 "export": {"file": "<CSV 文件路径>", "head": [["v1", "v2"], ...]}}
```

`export.file` 是本次返回行的 CSV，`export.head` 为前10行；`truncated=true` 表示输出截断，`data_scope.complete=false/null` 表示输入不完整或未知，聚合返回一行也不改变输入范围。按技能正文的完整性原则交回 SQL、CSV 路径和预览。

### ③-4 空结果复核（按具体疑点）

正式执行结果为空时，先核对原条件、时间范围和关联语义；报错时依据具体错误定位。只有值映射或类型等存在未决疑点，才补采相关列，不默认重复采样。

- 样本未包含某值，不证明源中没有该值；多个过滤值各自存在，也不证明其组合应有结果。
- 确认编码映射或输入错误后，保留原业务含义修正 SQL，并最多重试一次；若修正会改变用户条件，先澄清。
- 未发现可确认错误时，如实说明当前条件下返回零行及必要局限，不制造非空结果，也不把零行自动当成指标值为零。


## 返回与失败处理

- 成功：stdout 一个 JSON，退出码 0。失败：stderr 的 `error` 含 `kind/why/hint/retry`，退出码 1；SQL 脚本另带 `stage=SQL_EXPLAIN|SQL_EXECUTION`。
- `AMBIGUOUS_TABLE` 时按候选改全限定名；语法或列错误依据 hint 修 SQL。连接失败先恢复 web 服务，不改用直连或默认库。
