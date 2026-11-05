# 设计 token 与组件白名单

## 色板

| 用途 | 值 |
|---|---|
| 页面背景 | `#F7F8FA` |
| 卡片 | `#FFFFFF` |
| 边框 | `#E6E8EC` |
| 正文 | `#1F2329` |
| 次要文字 | `#5A6270` |
| 弱化文字 | `#8A94A6` |
| 主强调 | `#2F6BFF` |
| 正向 | `#1E8E5A` |
| 负向 | `#C6423F` |
| 警示 | `#B7791F` |

图表色板与页面 token 同源，最多 5 条序列；单序列图不显示图例。

## 版式

- 字体：系统无衬线栈；标题 600-680，正文 400；
- 页面最大宽度 1080px；卡片圆角 14px；阴影仅浅层；
- KPI 32px、章节标题 19px、正文 15px、图注 11px；
- 图表高度：普通 320px，趋势主图 420px，移动端 260/320px。

## 类名白名单

页面壳：`.hero` / `.hero-main` / `.eyebrow` / `.dek` / `.meta` / `.kpis` / `.kpi` /
`.bluf` / `.toc` / `.sec` / `.sec-head` / `.num` / `.conclusion` / `.narrative` /
`.chart` / `.chart-hero` / `.sql` / `.block` / `.callout` / `.actions` / `.limitations` /
`.foot`。

章节块：`.annotations` / `.annotation` / `.compare-head` / `.compare-col` / `.vs` /
`.diff-table` / `.banner` / `.banner-label` / `.banner-flow` / `.drivers` / `.driver` /
`.share` / `.alt-row` / `.verdict` / `.brief-list` / `.split` / `.metric-inline`。

## 禁止

- 在 spec 里写内联颜色或临时类名；
- 引入 CDN、外部字体、外部脚本；
- 深色主题（当前产品形态是浅色 + 打印）；
- 为单个报告改模板；需要新组件时先进本文件，再改 `assets/report.css`。

## 打印

- `@media print` 隐藏 TOC 与打印按钮，SQL 默认展开；
- 卡片、KPI、图表、块级元素 `break-inside: avoid`；
- 图表容器固定高度，避免打印时截断。
