# SQL 方言：Doris / MySQL


`execution_environment.sql_dialect` 为 `doris` 或 `mysql` 时适用；SQL-only 按 `target_sql_dialect` 选择。协议兼容不代表函数和版本能力相同，Doris 专用函数不要套用于 MySQL。

### 兼容 MySQL 但有差异的函数

| 函数 | 目标库 | MySQL | 备注 |
|---|---|---|---|
| `DATE_FORMAT` | ✅ | ✅ | 兼容 |
| `STR_TO_DATE` | ✅ | ✅ | 兼容 |
| `DATEDIFF` | ✅ | ✅ | 一致 |
| `TIMESTAMPDIFF` | ✅ | ✅ | 一致 |
| 分位数 | 按当前 Doris 版本核对 `PERCENTILE` / `APPROX_PERCENTILE` | 不假定支持同名聚合 | `APPROX_COUNT_DISTINCT` 是去重计数，不能替代分位数 |
| `LAG` / `LEAD` | ✅ | ✅ | 兼容 |
| `CTE` / `WITH` | ✅ | ✅ MySQL 8.0+ | 兼容 |

### Doris 专用能力（不要套用于 MySQL）

- `BITMAP_UNION` / `BITMAP_COUNT`：UV 类查询
- `LATERAL VIEW` + `explode`：数组展开
- `COLLECT_SET` / `COLLECT_LIST`：聚合收集

### 禁用 / 注意

- 明细查询限定范围和返回行数；聚合优先在 SQL 内完成，注意全表扫描代价
- 不用 `SELECT *`：列存只读所需列更快
- 不用 `PERCENTILE_APPROX`（目标库函数名是 `PERCENTILE` 或 `APPROX_PERCENTILE`）
- 时间列分桶可用 `DATE_FORMAT(col, '%Y-%m')`；Doris 支持的 `date_trunc(col, 'month')` 不直接套用于 MySQL，函数以目标版本能力为准。
- 目标方言保留字用作标识符时正确引用，或选择清楚且无歧义的别名；不将某个版本或语境中的错误泛化成所有库的禁用名称。
