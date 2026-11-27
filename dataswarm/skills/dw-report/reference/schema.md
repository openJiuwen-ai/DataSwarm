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
| `insights` | 否 | 综合洞察：字符串，或数组（元素为字符串或 `{text}` 对象） |
| `actions` | 否 | 行动建议：字符串，或数组（元素为字符串或 `{text, expected_impact}` 对象） |
| `limitations` | 否 | 口径与局限：字符串或数组，按实际局限填写 |
| `shape` | 否 | `chart` / `lite` / `full`；缺省自动判定页面 |
| `period` / `data_source` / `generated_at` | 否 | Hero 元信息与页脚 |
| `mode` | 否 | `open` / `closed`；分析报告均需证据校验 |
| `report_type` | 否 | `analysis`（默认，受会话证据门禁约束）或 `standalone`（独立 inline/sql 报告，不绑定会话） |
| `analysis_id` / `manifest_path` | 分析报告必填 | start 返回的分析身份与 finish 返回的快照路径；会话已启动分析时自动绑定，不可省略绕过 |
| `report_gate` | 只读 | 渲染器重新计算并回写，失败不生成产物 |

分析报告的 sections、summary、insights、actions 条目用 `goal_ids:["g1"]`，
声明本项结论或建议回答的业务目标；须同时有正文和有效 evidence_refs。
目标关联用于覆盖追踪；缺失时仅记录关联未确认，不自动认定业务未完成。实际任务缺口或显式partial声明仍保留。
已有数据支持的综合/建议目标无需另建取数任务；实际未完成的必要取数仍使报告 partial。
单个`goal_ids`字符串可自动归一为数组；KPI只展示数值，不接受`goal_ids`，目标关联写在实际解释或建议条目中。

分析证据模式下，`sections/kpis/summary/insights/actions`的单对象自动包为数组，
`evidence_refs`单个字符串自动包为数组；不猜造引用或改写业务内容。`limitations`
接受字符串或字符串数组，源证据已有的局限由服务补充，不要求作者逐字复制。

**分析证据模式的额外约束**：显式关联 manifest、声明分析模式/类型，或由当前会话
自动绑定分析时，`summary` / `insights` / `actions` 必须是对象数组；每项 `text`
必须为非空字符串，`evidence_refs` 必须为非空字符串数组。省略指整个字段不写或置
空数组；只要写了条目，`text` 与 `evidence_refs` 就都必须合法，缺一即整体退回。
`limitations` 继续支持自由文本。独立 standalone 和无会话绑定的旧式 inline/sql
报告保留上表的字符串兼容。

```json
{"summary": [{"text": "销售额为 100", "goal_ids": ["g1"], "evidence_refs": ["task1-0123456789abcdef"]}],
 "actions": [{"text": "重点关注头部区域，并跟踪下一期贡献变化", "goal_ids": ["g2"], "evidence_refs": ["task1-0123456789abcdef"]}]}
```

CLI `validate`只检查草稿结构，允许交付快照尚未产生；若显式提供身份路径或已有会话指针，则仍核验其合法性与归属。成功不代表可交付、证据仍有效、CSV 哈希一致
或覆盖完整。render 继续执行动态 gate、CSV 和图表检查。`data_evidence_ref` 若出现，
必须是该项 `evidence_refs` 之一。证据 ID 仅容忍唯一的连字符/下划线形态差异，精确匹配
优先；歧义、过期证据仍拒绝。归一化在解析数据前完成，审计记录随 `report_spec.json`
保存。dw-analysis 的 `analysis_task` 的 finish 在渲染前校验报告，失败保留运行状态供修订。

## section

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 是 | 本节标题；自动去掉「一、」类编号 |
| `conclusion` | 是 | 本节一句话结论 |
| `narrative` | 否 | 字符串或字符串数组 |
| `chart` | 分析报告至少一节必填 | option / `{type,x,y,color}` / data.json 的 id；仅剩服务批准的待核验草稿时可无图 |
| `chart_caption` | 否 | 图表说明；缺省用「图 N · 标题」 |
| `table` | 否 | `{columns, rows, max_rows}` |
| `supporting_tables` | 否 | 补充表格数组，每项为`{columns, rows, max_rows}`；与解释、主图同节，折叠展示 |
| `sql` | 否 | 仅HTML中的SQL折叠区；evidence模式从证据血缘回填，Markdown不显示 |
| `evidence_refs` | evidence 时必填 | 证据 id 列表 |
| `goal_ids` | 本节回答综合/建议目标时填写 | start.goals 对应的 g1/g2 等标识；与有效证据一起声明业务覆盖 |
| `data_evidence_ref` | 图表证据有歧义时必填 | 为 `evidence_refs` 之一；唯一匹配时可自动选定 |
| `block` | 否 | `trend` / `compare` / `attribution` / `table` / `chart` |
| `short` | 否 | TOC 短标签 |
| `annotations` | 否 | trend 块的时间锚点，见 blocks.md |
| `compare` / `diff_table` | 否 | compare 块的双栏与差异表 |
| `banner` / `drivers` / `alternatives` | 否 | attribution 块的异常信号、驱动、替代解释 |
| `claim_type` / `hypothesis` / `alternative_checked` | 否 | 深度要素；`alternative_checked` 自动进归因块 |
| `span` | 否 | `full` / `half` / `third`（预留） |

## KPI

`kpis`为可选对象数组，渲染在结论摘要前。使用`value_column`与`evidence_refs`从CSV取值；多行时必须用`row`唯一定位，多份证据用`data_evidence_ref`明确选择，不默认取末行。字段缺省及取值行为如下。

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

## 正文数字绑定

分析报告的`sections/summary/insights/actions/kpis`条目可声明`numeric_facts`对象，正文用`{{name}}`引用同条目的数值。关键数字使用该绑定，渲染器从已登记且hash一致的证据计算后填入HTML和Markdown，不调用模型、不取新数据。

每项数值包含`inputs`和`expression`，可选`digits`（0到10，默认2）、`format`（number/percent，默认number）、`value`（核验已有展示数值，可省略）。inputs每项含`evidence_ref`、`column`、可选`row`（从0起的行号或等值筛选对象）及`aggregate: "sum"`。条目只有一个evidence_refs时可省略输入的evidence_ref；多证据不猜选。未指定sum时必须唯一匹配一行。expression仅支持这些变量、常数、括号和加减乘除；percent将比值乘100并带百分号，value按展示单位和digits舍入容差校验。

```json
{"title":"地区占比","conclusion":"本地区占比为{{share}}。","evidence_refs":["ev-total"],
 "numeric_facts":{"share":{"inputs":{
   "part":{"column":"amount","row":{"region":"east"}},
   "whole":{"column":"amount","aggregate":"sum"}},
   "expression":"part / whole","format":"percent","digits":1}}}
```

sum要求证据的`data_scope.complete=true`，不把样本或完整性不明的数据合计当作完整分母。缺列、匹配为空或多行、空值、非有限值、零分母、未绑定占位符或value不符会返回具体错误，沿用两轮修订上限，到限将整个未通过条目标为待核验草稿。权限、跨分析文件及hash不符仍拒绝。已验证部分照常生成报告。

检查保证指定取值与公式的数值一致，不推断自由文字含义，不证明模型选择的业务字段/分母合理。术语、因果、解释性结论不由此自动认证。

## 三种数据来源

| 来源 | 判定 | 行为 |
|---|---|---|
| evidence | `analysis_id` + `manifest_path`（closed/open 均适用；会话可自动绑定） | 从 manifest 登记的 CSV 构图/取值；校验 sha256 |
| sql | section 或顶层有 `sql`，且传 `--db` | 经 dwcli 取数 |
| inline | `report_type="standalone"` 且无 manifest | 图表 option 直接透传，或从 `--data data.json` 取 id；不读取当前会话分析指针 |

独立报告显式设置 `report_type: "standalone"`，可使用 inline、`--data` 或 section.sql + `--db`。
该类型不能同时携带 `analysis_id`、`manifest_path`、分析 `mode`、`report_gate` 或任何 `evidence_refs/data_evidence_ref`。
无会话分析时，省略 `report_type` 的既有 inline/sql 调用继续可用；会话已有分析时须显式声明独立类型。

evidence 模式渲染后，原始 spec 与门禁结果会回写到 manifest 同目录
（`report_spec.json`、`analysis.json` 的 `report_gate` 字段），供事后重判；
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
  "manifest_path": "/path/analysis.json",
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

章节perspective_ids可选，仅用于覆盖追踪；缺省时仅补唯一归属，不将共享证据自动视为回答全部视角；关联未确认只提示，不阻断交付或据此降为partial。任务或目标存在实际缺口时服务自动标partial，模型主动声明的partial保留。图表匹配唯一CSV自动绑定，多候选只需补data_evidence_ref。attribution/compare章节可以只用文字或表格，不因块类型强制出图。

## 图表展示数据选择

CSV图表可在chart中加入data_view，支持筛选/排序/截取，不执行SQL、Python或聚合，不改变原CSV与哈希。过滤列也必须在data_evidence_ref指定的证据中。filters按顺序取交集，op仅in/not_in，values为实际标量值数组；sort使用实际列并可descending，limit为1到500。筛选为空或列不存在返回具体错误。

```json
{"chart":{"type":"bar","x":"region","y":"change","data_view":{
  "filters":[{"field":"row_kind","op":"in","values":["detail"]}],
  "sort":{"field":"change","descending":true},"limit":10}}}
```

`y`可为实际数值列名的数组，表示多个序列。HTML画图；Markdown用相同选择后的数据表呈现，默认最多20行。HTML保留SQL折叠入口；Markdown不输出SQL、计算说明或HTML折叠标签。
