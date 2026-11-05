# 页面与章节块

## 页面级（3 种）

| 页面 | 判定 | 结构 |
|---|---|---|
| `answer` | 1 节、无 KPI、无 summary；或 `shape=chart` | Hero + 单图 + 一句结论 |
| `brief` | `shape=lite`；或 ≤2 节、≤3 KPI、无图、无洞察 | Hero + KPI + 三件事 + 需要关注 + 动作 |
| `analysis` | 其余 | Hero + KPI + 结论摘要 + TOC + N 节 + 洞察/行动/局限 |

## 章节块（5 种）

判定优先级：`attribution → compare → trend → table → chart`。
`section.block` 显式指定时直接采用。

### attribution

触发：`alternative_checked` 非空、`claim_type=interpretation`、`drivers` 或 `banner`。

```json
{
  "block": "attribution",
  "banner": {"label": "异常信号", "value": "+12.4% MoM", "flow": "590k → 664k BRL"},
  "drivers": [
    {"share": "51%", "label": "SP 州", "value": "+38k BRL", "tag": "主因"}
  ],
  "alternatives": [
    {"verdict": "排除", "label": "促销时点", "text": "7 月无大促，时间上早于任何促销动作。"},
    {"verdict": "部分", "label": "季节性", "text": "去年同期 +6.1%，只能解释约一半。"}
  ]
}
```

`verdict` 可用 `排除` / `部分` / `支持`；`alternative_checked` 字符串会按
「标签：说明」自动拆成同样的行。

### compare

触发：`compare` / `diff_table`，或标题含「对比 / 比较 / 差异 / VS」。

```json
{
  "block": "compare",
  "compare": {
    "a": {"label": "SP", "value": "281k", "unit": "BRL",
          "items": ["环比 +12.4%", "订单 3,912 单，+9.8%"]},
    "b": {"label": "RJ", "value": "85k", "unit": "BRL",
          "items": ["环比 +9.0%", "订单 1,204 单，+0.8%"]}
  },
  "diff_table": {
    "columns": ["指标", "SP", "RJ", "差异", "解读"],
    "rows": [["GMV 增速", "+12.4%", "+9.0%", "+3.4pp", "SP 增长更快"]]
  }
}
```

### trend

触发：`chart` 的 series 为 `line` / `area`，或 x 轴是时间序列。

```json
{
  "block": "trend",
  "chart": {"type": "line", "x": "month", "y": "gmv"},
  "annotations": [
    {"date": "7 月", "title": "加速起点", "text": "GMV 590k，环比 +28%。"}
  ]
}
```

### table

触发：只有明细表、没有图。

```json
{
  "block": "table",
  "table": {"columns": ["支付方式", "占比"], "rows": [["信用卡", "78.3%"]], "max_rows": 12}
}
```

### chart

其他有图节：图 + 结论 + 解读；`table` 字段存在时图后追加明细表。

## 写作约束

- 标题写结论，不写「XX 分析」；
- 结论句带数字，数字必须能回链证据；
- 归因块必须有替代解释，不能只给主因；
- 对比块必须回答「差异意味着什么」，不能只列两列数字；
- 趋势块的时间锚点只标真正的转折，不要每个点都标。
