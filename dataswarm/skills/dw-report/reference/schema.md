# spec 字段契约

字段契约：必填字段尽量少，未知字段透传。

## 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 是 | 报告主标题 |
| `sections` | 是 | 至少 1 节 |
| `subtitle` | 否 | 副标题 / 口径说明 |
| `summary` | 否 | 结论摘要（BLUF），`analysis` 页面渲染 |
| `kpis` | 否 | KPI 数组，见下 |
| `insights` | 否 | 综合洞察，字符串或 `{text}` |
| `actions` | 否 | 行动建议，字符串或 `{text, expected_impact}` |
| `limitations` | 否 | 口径与局限，2-3 条 |
| `shape` | 否 | `chart` / `lite` / `full`；缺省自动判定页面 |
| `period` / `data_source` / `generated_at` | 否 | Hero 元信息与页脚 |
| `mode` | evidence 时必填 | `open` |
| `analysis_id` / `manifest_path` | evidence 时必填 | 与 report-gate 一致 |
| `report_gate` | 只读 | 由 report-gate 写入，渲染器不改 |

## section

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 是 | 结论式标题；自动去掉「一、」类编号 |
| `conclusion` | 是 | 本节一句话结论 |
| `narrative` | 否 | 字符串或字符串数组 |
| `chart` | 否 | option / `{type,x,y,color}` / data.json 的 id / 空 |
| `chart_caption` | 否 | 图表说明；缺省用「图 N · 标题」 |
| `table` | 否 | `{columns, rows, max_rows}` |
| `sql` | 否 | SQL 折叠区；evidence 模式从证据回填 |
| `evidence_refs` | evidence 时必填 | 证据 id 列表 |
| `data_evidence_ref` | evidence 时必填 | 必须是 `evidence_refs` 之一 |
| `block` | 否 | `trend` / `compare` / `attribution` / `table` / `chart` |
| `short` | 否 | TOC 短标签 |
| `annotations` | 否 | trend 块的时间锚点，见 blocks.md |
| `compare` / `diff_table` | 否 | compare 块的双栏与差异表 |
| `banner` / `drivers` / `alternatives` | 否 | attribution 块的异常信号、驱动、替代解释 |
| `claim_type` / `hypothesis` / `alternative_checked` | 否 | 深度要素；`alternative_checked` 自动进归因块 |
| `span` | 否 | `full` / `half` / `third`（预留） |

## KPI

| 字段 | 必填 | 说明 |
|---|---|---|
| `label` | 是 | 指标名 |
| `value` | 否 | 已算好的值；缺省时从证据 CSV 读取 |
| `value_column` | 证据取值时必填 | CSV 列名 |
| `unit` | 否 | 单位 |
| `delta` / `tone` | 否 | 环比文案与语义色（`up` / `down` / `flat`） |
| `evidence_refs` | 证据取值时必填 | 证据 id |
| `row` | 否 | 多行 CSV 的取值行：`{"month": "2025-08"}` 或行号 |
| `sql` | 否 | 看板导入用 |

## 三种数据来源

| 来源 | 判定 | 行为 |
|---|---|---|
| evidence | `mode=open` + `manifest_path` | 从 manifest 登记的 CSV 构图/取值；校验 sha256 |
| sql | section 或顶层有 `sql`，且传 `--db` | 经 dwcli 取数 |
| inline | 无 manifest / sql | 图表 option 直接透传，或从 `--data data.json` 取 id |

evidence 模式渲染后，原始 spec 与门禁结果会回写到 manifest 同目录
（`report_spec.json`、`manifest.json` 的 `report_gate` 字段），供事后重判；
正文产物不含这些审计信息。

`chart` 的三种写法：

```json
{"type": "line", "x": "month", "y": "gmv", "color": "可选分组列"}
```

```json
{"baseOption": {"series": [{"type": "line", "data": [1, 2]}]}}
```

```json
"trend_option"
```

## 最小示例

```json
{
  "title": "8 月经营分析",
  "mode": "open",
  "analysis_id": "analysis-...",
  "manifest_path": "/path/manifest.json",
  "kpis": [
    {"label": "8 月 GMV", "value_column": "gmv", "unit": "k",
     "evidence_refs": ["ev-trend-1"], "row": {"month": "2025-08"}}
  ],
  "sections": [
    {
      "title": "月度 GMV 走势",
      "conclusion": "8 月 GMV 达 664k，创年内新高。",
      "narrative": ["1–6 月震荡，7–8 月连续加速。"],
      "evidence_refs": ["ev-trend-1"],
      "data_evidence_ref": "ev-trend-1",
      "chart": {"type": "line", "x": "month", "y": "gmv"}
    }
  ],
  "limitations": ["仅覆盖 2025 年，未做去年同期对比。"]
}
```
