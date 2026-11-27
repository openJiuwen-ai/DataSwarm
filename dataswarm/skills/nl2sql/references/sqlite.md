# SQL 方言：SQLite

`execution_environment.sql_dialect="sqlite"` 时适用；SQL-only 按 `target_sql_dialect` 选择。使用召回返回的实际表列，不把数据库文件名当表名。

| 需求 | 写法 |
|---|---|
| 标识符 | 双引号，名称内的双引号写为两个双引号 |
| 月份 | `strftime('%Y-%m', time_column)`，格式串在前 |
| 日期调整 | `date(time_column, '+1 month')` |
| 日期差 | `julianday(end_column) - julianday(start_column)` |
| 非整数除法 | `1.0 * numerator / NULLIF(denominator, 0)` |
| 数值转换 | `CAST(value AS REAL)`；不要使用 `TRY_CAST` |
| 条件聚合 | `SUM(CASE WHEN condition THEN amount ELSE 0 END)` |

时间范围按实际存储格式构造左闭右开条件。SQLite没有MySQL的`DATE_FORMAT`，也没有DuckDB的`date_trunc`、`UNNEST`或原生分位数聚合；复杂统计可对完整查询结果使用计算能力。
