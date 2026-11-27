# SQL 方言：DuckDB


`execution_environment.sql_dialect` 为 `duckdb` 时适用，包括由其他源物化后在 DuckDB 执行的查询；SQL-only 按 `target_sql_dialect` 选择。不要仅凭 `source_type` 推断执行语法。

### 与 Doris 习惯的关键差异

| 主题 | DuckDB 正确写法 | Doris 习惯（此处禁用） |
|---|---|---|
| 时间格式化 | `strftime(col, '%Y-%m')` | `DATE_FORMAT(col, '%Y-%m')` |
| 时间截断 | `date_trunc('month', col)` ——**参数序相反，粒度在前** | `date_trunc(col, 'month')` |
| 数组展开 | `CROSS JOIN UNNEST(arr) AS t(x)` | `LATERAL VIEW` + explode |
| 近似/去重计数 | `COUNT(DISTINCT x)` | `APPROX_COUNT_DISTINCT` / `BITMAP_UNION` |
| 聚合收集 | `list(x)` / `list(DISTINCT x)` | `COLLECT_LIST` / `COLLECT_SET` |
| 近似分位数 | `approx_quantile(x, 0.95)` | `PERCENTILE` / `APPROX_PERCENTILE` |
| 标识符引号 | 双引号 `"col"` | 反引号 `` `col` `` |
| 日期差 | `date_diff('day', a, b)` ——**间隔单位在前，且参数序与 MySQL DATEDIFF 相反** | `DATEDIFF(a, b)` / `DATEDIFF(b, a)` |

### DuckDB 可用特性

- 窗口函数 / CTE / `WITH` / `LAG` / `LEAD`：兼容
- `CAST(x AS DOUBLE)` / `TRY_CAST`：可用（`TRY_CAST` 失败返回 NULL 而非报错，脏数据友好）
- 正则：`regexp_matches(col, 'pattern')`、`regexp_extract(col, 'pattern', i)`

### 禁用 / 注意

- 明细查询限定范围和返回行数；聚合优先在 SQL 内完成，注意文件全量扫描代价
- 不用 `SELECT *`：只读所需列
- 文件视图不提供关系库式主键约束保证；按真实实体关系、粒度与基数选择 JOIN 键，不凭低重复率推断业务关联。
- `Catalog Error`、`ParserException`、`Binder Error` 可能来自对象名、语法、类型或函数签名；依据具体错误与返回目录修正，不统一归因于方言，也不原样重试。
- 文件源列类型由 `read_csv` 嗅探决定，日期列可能是 VARCHAR：比较前用 `TRY_CAST` / `strptime` 显式转型，不要直接与日期字面量比较
