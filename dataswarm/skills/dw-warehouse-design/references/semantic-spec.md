# 语义规格（Semantic Spec）编写规范

L2/L3 阶段 LLM 的**唯一交付物**：只描述"表/列/JOIN/分组/表达式"等语义信息。
DDL 与 ETL SQL 由 `scripts/plan.py` 的渲染器确定性生成——**不要在规格里写任何 CREATE TABLE / INSERT 语句**。

> **本文档即契约**：写规格只看本文档，禁止通读 `scripts/` 源码核对字段。脚本内部实现字段可能与 JSON 字段不同名（如 join 类型：JSON 字段是 `type`，代码内部叫 `kind`，二者同义）——JSON 一律按本文档的字段名写。

> **调度可配置**：顶层可选字段 `schedule_cron`（5 段 cron：分 时 日 月 周）。用户提出调度诉求时**必须**换算写入（如『每天凌晨 3 点』→ `"0 3 * * *"`、『每小时』→ `"0 * * * *"`）；缺省 `"0 2 * * *"`（每天 02:00 T+1）。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 方案名（如 `olist_sales`） |
| `business` | ✅ | 业务域（如 `olist`） |
| `purpose` | ✅ | 方案目的（一句话） |
| `tables` | ✅ | 派生表数组（只含 DWD/DWS/ADS；ODS 由脚本自动生成） |
| `metrics` | ❌ | **指标驱动模式（推荐）**：指标声明数组；声明后 DWS/ADS 表由脚本自动推导，无需手写这些表 |
| `schedule_cron` | ❌ | 调度 cron（5 段：分 时 日 月 周），如 `"0 3 * * *"`；用户有调度要求时写入，缺省 `"0 2 * * *"`（每天 02:00） |

## tables[] 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `short_name` | ✅ | 短表名，必须以 `dwd_` / `dws_` / `ads_` 前缀开头且全规格内唯一 |
| `layer` | ✅ | `DWD` / `DWS` / `ADS` |
| `columns` | ✅ | 目标列数组：`{name, dtype, nullable, comment?}`；dtype 用 Doris 类型（`BIGINT/DECIMAL(P,S)/DOUBLE/DATE/DATETIME/BOOLEAN/VARCHAR(n)/STRING`），**方言差异由渲染器映射**（MySQL 后端 `STRING`/超长 `VARCHAR` 自动转 `TEXT`，`DECIMAL` 上限收敛）。**金额/单价/支付值必须用 `DECIMAL`**（如 `DECIMAL(18,6)`），DOUBLE 仅用于比率/评分等近似值 |
| `source_tables` | ✅ | 源表全名数组`<业务库名>.<表名>`；只能引用 plan 内 ODS、已复用的 ODS、或规格内更下层的表 |
| `source_aliases` | ✅* | `{全名: 别名}`；有 joins 或 column_exprs 时必须覆盖全部源表 |
| `joins` | ❌ | 数组：`{left, right, on: [{left_col, right_col}], type: "left"|"inner"}` |
| `column_exprs` | ✅* | `{目标列: SQL 表达式}`；**非 group_by 列必须显式给表达式**；聚合直接写（如 `COUNT(DISTINCT d.`id`)`） |
| `group_by` | ❌ | 分组列名数组（DWS/ADS 常用）；分组列可省略 expr（默认 `别名.列`） |
| `where` | ❌ | 过滤条件（不含 WHERE 关键字） |
| `comment` | ❌ | 表注释 |

## 层级依赖规则（硬约束）

| 目标层 | 允许的源层 |
|---|---|
| DWD | ODS |
| DWS | ODS / DWD |
| ADS | DWD / DWS |

禁止 ADS 直源 ODS、禁止同层引用。`plan.py` 校验失败会整体拒绝。

## 指标驱动模式（推荐：先指标，后分层）

业务诉求是"看某些指标"时，**优先声明 metrics[] 而不是手写 DWS/ADS 表**：
LLM 只把业务话术翻译成指标声明（名称/口径/粒度/表达式/来源），分层表由脚本确定性推导。

### metrics[] 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `metric_name` | ✅ | 指标英文名（合法标识符，全规格唯一），如 `gmv` |
| `display_name` | ❌ | 中文名（如 `商品交易总额`），落列 COMMENT |
| `caliber` | ❌ | 口径说明（自然语言），落列 COMMENT，供治理 |
| `value_type` | ❌ | 指标值类型，默认 `DOUBLE`；**金额类聚合指标（SUM/AVG）必须显式 `DECIMAL(38,S)`**——Doris 聚合中间类型即 `DECIMAL(38,S)`，窄列全区间累计会运行时溢出（E-255）；分组聚合按“源列整数位数 + log10(组内行数上界)”估容量，拿不准一律 `38`；比率/占比用 `DOUBLE` |
| `expr` | ✅ | 聚合表达式（如 `SUM(o.`amount`)`），别名须与 source_aliases 一致；`别名.列` 的列名必须真实存在于来源表（ADS 引用 DWS 时须用 `metric_name` 全名列） |
| `grain` | ✅* | 聚合粒度列数组（如 customer_id + dt），类型与可空性自动从来源表继承 |
| `grain_exprs` | ❌ | 粒度列清洗表达式映射（如 `COALESCE(o.`x`, 'unknown')`），同时用于 SELECT 与 GROUP BY |
| `source_tables` | ✅ | 指标来源（ODS / 规格 DWD / 已推导 DWS 的全名） |
| `source_aliases` | ✅ | 来源别名映射（有 expr 时必须覆盖全部来源） |
| `table_name` | ✅ | 指标落地表（`dws_` / `ads_` 前缀决定层级） |
| `where` | ❌ | 过滤条件（不含 WHERE 关键字） |
| `dimensions` | ❌ | 可下钻维度；必须落在 grain 或来源表列中 |

### 推导规则（代码完成，LLM 不参与）

1. **同表合并**：`table_name` 相同的指标合入同一张表，但必须同 grain / 同 source_tables / 同 where，否则拒绝。
2. **两遍推导**：先 DWS（可源 ODS 或 DWD），后 ADS（可引用刚推导出的 DWS）。
3. **粒度列继承类型与可空性**：来源列可空（如 LEFT JOIN 未命中）→ 粒度列可空，避免 Doris 严格模式把 NULL 写入 NOT NULL 列触发 `DATA_QUALITY_ERROR`；此时输出设计期 warning，如需清洗用 `grain_exprs` 包 COALESCE。
4. **GROUP BY 自动生成**：按 grain 生成（有 grain_exprs 用其表达式）；ADS 全局指标 grain 可为空（无分组单行汇总），此时自动生成首列 `stat_date DATE NOT NULL`（取值 `CURRENT_DATE`）作为 Doris key。
5. **指标来源单表**：跨表指标请先在 `tables[]` 设计 DWD 宽表，再让指标引用该 DWD（多源指标会被校验拒绝）。
6. **指标覆盖校验**：build 输出的 metrics 清单标明每个指标落在哪张表，用户可核对无遗漏。

### 示例（GMV + 订单数 → 一张 DWS；复购率 → ADS 引用该 DWS）

```json
{
  "name": "olist_sales_metrics",
  "business": "olist",
  "purpose": "销售指标建设",
  "metrics": [
    {
      "metric_name": "gmv",
      "display_name": "商品交易总额",
      "caliber": "剔除取消订单的支付金额总和",
      "value_type": "DOUBLE",
      "expr": "SUM(o.`amount`)",
      "grain": ["customer_id", "dt"],
      "source_tables": ["<biz>.ods_orders"],
      "source_aliases": {"<biz>.ods_orders": "o"},
      "table_name": "dws_user_order_1d",
      "where": "o.`status` <> 'canceled'",
      "dimensions": ["customer_id", "dt"]
    },
    {
      "metric_name": "order_cnt",
      "display_name": "订单数",
      "value_type": "BIGINT",
      "expr": "COUNT(DISTINCT o.`order_id`)",
      "grain": ["customer_id", "dt"],
      "source_tables": ["<biz>.ods_orders"],
      "source_aliases": {"<biz>.ods_orders": "o"},
      "table_name": "dws_user_order_1d",
      "where": "o.`status` <> 'canceled'"
    },
    {
      "metric_name": "repurchase_rate",
      "display_name": "复购率",
      "value_type": "DOUBLE",
      "expr": "SUM(CASE WHEN w.`order_cnt` > 1 THEN 1 ELSE 0 END) / COUNT(*)",
      "grain": [],
      "source_tables": ["<biz>.dws_user_order_1d"],
      "source_aliases": {"<biz>.dws_user_order_1d": "w"},
      "table_name": "ads_user_rfm"
    }
  ]
}
```

## 常见坑（渲染器已固化处理，规格里仍需注意）

1. **时间列类型匹配**：ODS 已是 `DATE/DATETIME` 类型的时间列直接引用，不要包 `NULLIF(col, '')`；仅当来源列是 `VARCHAR` 且需要转换为 `DATE/DATETIME` 时，才使用 `NULLIF(col, '')`（Doris 空串 CAST 会整行报错）。
2. **group_by 完整性**：SELECT 中所有非聚合列必须出现在 group_by 里，否则 SQL 报错。
3. **别名一致**：`column_exprs` / `joins` 中用的别名必须与 `source_aliases` 定义一致。
4. **列引用一致**：`expr` / `column_exprs` / `where` / `grain_exprs` 中 `别名.列` 的列名必须真实存在于来源表（build 会校验并整体拒绝，附最接近可用列提示）。**指标驱动模式下 DWS 落列名是 `metric_name`**（如 `board_total_amount`），ADS 引用该 DWS 时表达式必须写全名列（如 `m.`board_total_amount``），不能写业务短名（如 `m.`total_amount``）。
5. **列名合法**：`^[A-Za-z_][A-Za-z0-9_]*$`，且表内唯一。
6. **金额精度**：DOUBLE 是二进制浮点，`SUM` 聚合有累积误差、对账判等会失败——金额列一律 `DECIMAL(P,S)`（ODS 推断已默认 DECIMAL；DWD/MetricSpec 手写 dtype/value_type 时同样遵守）。**聚合容量**：`SUM/AVG(DECIMAL(P,S))` 的 Doris 中间类型是 `DECIMAL(38,S)`，目标列窄于它且结果超上限会运行时溢出（E-255）——聚合指标 `value_type` 直接用 `DECIMAL(38,S)`；构建器会把窄精度的聚合 DECIMAL 自动加宽到 `DECIMAL(38,S)` 并给 warning。

## 示例

### DWD（JOIN 明细宽表）

```json
{
  "name": "olist_sales",
  "business": "olist",
  "purpose": "订单销售主题",
  "tables": [
    {
      "short_name": "dwd_order_detail",
      "layer": "DWD",
      "comment": "订单明细宽表",
      "columns": [
        {"name": "order_id", "dtype": "VARCHAR(64)", "nullable": false},
        {"name": "customer_id", "dtype": "VARCHAR(64)"},
        {"name": "purchase_ts", "dtype": "DATETIME"},
        {"name": "price", "dtype": "DOUBLE"}
      ],
      "source_tables": ["<biz>.ods_orders", "<biz>.ods_order_items"],
      "source_aliases": {"<biz>.ods_orders": "o", "<biz>.ods_order_items": "i"},
      "joins": [
        {
          "left": "<biz>.ods_orders",
          "right": "<biz>.ods_order_items",
          "on": [{"left_col": "order_id", "right_col": "order_id"}],
          "type": "left"
        }
      ],
      "column_exprs": {
        "order_id": "o.`order_id`",
        "customer_id": "o.`customer_id`",
        "purchase_ts": "o.`order_purchase_timestamp`",
        "price": "i.`price`"
      },
      "group_by": [],
      "where": "o.`order_status` <> 'canceled'"
    }
  ]
}
```

### DWS（聚合汇总）

```json
{
  "short_name": "dws_user_order_1d",
  "layer": "DWS",
  "columns": [
    {"name": "customer_id", "dtype": "VARCHAR(64)", "nullable": false},
    {"name": "order_cnt", "dtype": "BIGINT"},
    {"name": "gmv", "dtype": "DOUBLE"}
  ],
  "source_tables": ["<biz>.dwd_order_detail"],
  "source_aliases": {"<biz>.dwd_order_detail": "d"},
  "joins": [],
  "column_exprs": {
    "order_cnt": "COUNT(DISTINCT d.`order_id`)",
    "gmv": "SUM(d.`price`)"
  },
  "group_by": ["customer_id"]
}
```

## 校验失败处理

`plan.py --action plan` 返回 `{ok: false, errors: [...]}` 时，逐条修复规格 JSON 后重跑；**禁止**绕过校验手改 plan 或手写 SQL。
