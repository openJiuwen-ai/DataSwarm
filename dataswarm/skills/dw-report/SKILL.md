---
name: dw-report
description: 数据分析报告的统一渲染出口——吃 dw-analysis 产出的报告 spec，按「页面 + 章节块」两级骨架生成单文件 HTML 与 Markdown。当用户要「出一份分析报告 / 经营分析 / 周报 / 趋势解读 / 异动归因 / 对比分析 / 画个图 + 结论」时使用。
allowed_tools: [code, bash, read_file, write_file]
---

# dw-report —— 分析报告渲染器

输入是 **报告 spec（JSON）**，输出是 **单文件 HTML + Markdown**。渲染全程确定性：
LLM 只写 spec 文案与结构，图表与页面由脚本生成；同一份 spec 永远得到同一份产物。

## 何时用

- 用户要一份数据分析报告、经营分析、周报/月报、趋势解读、异动归因、对比分析；
- 上游 `dw-analysis` 已给出 manifest / CSV / 报告规格，需要组装成交付物；
- 用户要「一张图 + 一句话结论」，也走本 skill 的 `answer` 页面。

## 三条命令

```bash
# 1. 校验 spec
python <skill_dir>/scripts/dw_report.py validate --spec report_spec.json

# 2. 渲染（证据 / SQL / 内联三种来源）
python <skill_dir>/scripts/dw_report.py render \
  --spec report_spec.json [--data data.json] [--db <db-ref>] \
  [--html report.html] [--md report.md] [--strict]

# 3. 单独校验已有 HTML
python <skill_dir>/scripts/dw_report.py verify --html report.html --min-charts 1
```

`render` 已内置 ECharts 内联与产物门禁；成功时打印 path-mode JSON
（`page` / `formats` / `html_path` / `md_path` / `warnings`）。默认落盘
`~/.dataswarm/artifact/analysis/report_<ts>.{html,md}`。

## 两级骨架

| 页面级 | 判定 | 结构 |
|---|---|---|
| `answer` | 1 节、无 KPI、无 summary | 标题 + 单图 + 一句结论 |
| `brief` | `shape=lite`，或 ≤2 节/≤3 KPI/无图 | 标题 + KPI + 三件事 + 关注 + 动作 |
| `analysis` | 其余 | 标题 + KPI + 结论摘要 + TOC + N 节 + 洞察/行动/局限 |

| 章节块 | 判定 | 结构 |
|---|---|---|
| `attribution` | `alternative_checked` / `claim_type=interpretation` / `drivers` / `banner` | 异常信号 + 拆解图 + 驱动卡 + 替代解释 |
| `compare` | `compare` / `diff_table` / 标题含「对比」 | 双栏对照 + 差异表 + 结论 |
| `trend` | 折线图或时间轴 | 主图 + 时间锚点 + 解读 |
| `table` | 只有明细表 | 表 + 结论 |
| `chart` | 其他有图节 | 图 + 结论 + 解读 |

判定优先级：`attribution → compare → trend → table → chart`。需要强制指定时在
section 写 `"block": "compare"`。

## 红线

1. **字段契约从简**：必填字段尽量少，未知字段透传；同一份 spec 永远得到同一份产物。
2. **数字不手写聚合**：派生数字（份额、环比、合计）由 resolver 从 CSV 计算；
   KPI 用 `value_column` + `evidence_refs`，不誊写数值。
3. **证据优先**：`mode=open` 时 section 禁写 `sql`，渲染器从证据回填；
   `--strict` 下证据缺失或哈希不符直接失败。
4. **产物自包含**：HTML 落盘后必须过门禁（无外链、图表实例、体积 ≤6MB），
   失败不得交付。
5. **path-mode 出口**：响应正文只给路径，不粘报告本体。
6. **不读 `scripts/*.py` 源码找参数**：用法以本文件与 `reference/` 为准；
   脚本报错按 stderr 修正参数，仍不通则上报缺口。

## 文档

- `reference/schema.md`：spec 全字段与三种数据来源
- `reference/blocks.md`：页面/章节块的数据形态与判定
- `reference/quality.md`：结论、数字、建议、局限的写作约束
- `reference/themes.md`：设计 token 与组件类名白名单
