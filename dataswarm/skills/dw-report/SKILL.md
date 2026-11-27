---
name: dw-report
description: 将已有必要数据、结论、证据和报告 spec 直接渲染为 HTML 与 Markdown，不因报告形式启动分析。仅缺数据先用专业查询；需要新的表现解释、原因诊断或综合分析时由 dw-analysis 编排。
allowed_tools: [code, bash, read_file, write_file]
---

# dw-report

分析报告引用实际计算或查询证据，图表用于支撑解释。HTML的SQL折叠入口保留每条独立来源SQL；Markdown只呈现业务内容，不输出SQL、计算方法、指标说明或HTML折叠标签。计算过程、参数和内部ID留在分析追踪与证据文件，必要口径、单位和数据局限用业务语言交代。

根据用户需要理解的关系选择数值、图表或表格，并配业务解释。分析报告至少包含一张基于有效证据、支撑核心判断的图表；先识别需要读者理解的关系，再按实际数据选择图型。能帮助读者一眼理解答案的关键数值优先用指标卡呈现在摘要前。呈现数据应具有可比较的口径、单位和粒度，必要时从已有证据选择合适的数据子集并说明展示范围。其余形式、指标名称、数量和组合由当前问题与证据决定，不套报告类型模板，也不为排版新增取数。仅剩服务标记的待核验草稿时保留缺口，不编造图表。

输入报告 spec JSON，输出自包含 HTML + Markdown。LLM 写结构与文案，脚本确定性生成图表和页面；本技能生成并落盘，交付由调用方负责。

纯排版先核验已有数据与结论，只用已有 inline/CSV/证据引用，不在 resolver 中添加 SQL 偷补数据。输入缺失返回具体缺口，不自动启动分析；用户要求不再取数时不得查询。查询后指定出图可直接接本技能。分析报告继续使用 analysis 类型与原证据门禁，不能改成 standalone 绕过检查。

## 工作流与命令

先用 `skill_tool(skill_name="dw-report")` 取得 `skill_directory`。命令工具显式 `shell_type: bash`。

1. 写 spec 前读 [schema.md](reference/schema.md) 与 [quality.md](reference/quality.md)：按数据来源填写字段，用证据支撑数字与结论。
2. 分析会话直接把spec交给`analysis_task finish`；独立报告直接调用一次`render --spec ... --html ... --md ...`。同一份内容由脚本生成HTML与Markdown，并内置输入校验、证据/数字检查和HTML产物检查；正常交付不单独调用validate或verify。
3. 以成功回包的`validation`与产物路径为准交付，附简短结论。只有返回具体错误、用户要求复核或需要修正内容时才检查相关输入/产物并重跑；成功后不例行回读全文、改写或再次渲染。非0退出不交付。分析报告关联不完整时服务返回具体缺口，允许两轮修订；到限后保留已验证部分，将未解决条目标为待核验草稿并生成部分报告。不要换ID重置修订次数，不把待核验条目当成已证实结论。

```bash
python "{skill_directory}/scripts/dw_report.py" render --spec report_spec.json --html report.html --md report.md [--data data.json] [--db <db-ref>] [--strict]
```

`render` 成功返回 path-mode JSON：`page/formats/html_path/md_path/validation/warnings`。`validation`分别标记spec、HTML和Markdown的检查结果，未请求的格式标记not_requested；仅确认程序检查，不认证自由文本含义。默认落盘 `~/.dataswarm/artifact/analysis/report_<ts>.{html,md}`；自定义数据根目录按平台环境解析。使用返回路径，不猜文件名。

调试未完成草稿时可用`validate --spec ...`；复验已有或被修改的HTML时可用`verify --html ... [--min-charts N]`。validate通过只代表草稿结构可用，不代表可交付。render/finish中的分析证据始终严格校验，省略 `--strict` 不会关闭门禁；该参数保留兼容，独立报告不使用分析证据。

## 必须保持的约束

- **证据绑定**：分析报告使用 `report_type: "analysis"`，携带 `analysis_task finish` 返回的 `analysis_id/manifest_path`；已验证结论、图表、KPI引用 `evidence_refs`。跨会话、跨空间、哈希不符、撤销或过期证据继续拒绝。普通关联缺口按服务有限修订与部分交付处理，待核验标记由服务生成，不自行填写绕过校验。整理独立inline/sql数据显式用standalone，不携带分析身份或证据引用。口径选择及影响由服务补充至局限。
- **数字可复核**：直接取值与派生计算都引用实际证据；新增比率、剩余分组合计、极值或“全部/只有”判断先用完整适用数据计算核验，再写正文。摘要中的综合比较逐指标核对方向与范围，不将部分改善概括成“均优于”；摘要不得强于正文或所列局限。分析会话用compute-results，独立CSV用data-insight；不靠正文手算代替已有计算能力。区分总体、子集与分母，沿用用户及已确认业务定义，其他口径只作明确命名的辅助比较。取值字段见[schema.md](reference/schema.md)。展示通常最多两位小数并去尾零；极小非零值或用户要求的高精度保留必要有效数字，舍入不改原始证据和中间计算。
- **产物可交付**：HTML 不含外链资源、图表实例有效、体积 ≤6MB；分析报告校验至少一张实际生成的图表，声明的图表必须生成，失败先修正。
- **路径出口**：返回产物路径与必要警告，不粘贴报告本体。仅按文档参数执行；报错依据 stderr 修正，仍不通则报告缺口，不另建渲染脚本。

分析报告正文中的关键数字用`{{name}}`占位符与同条目的`numeric_facts`绑定证据行/列和算术表达式，由程序计算填入；不另抄一次计算结果。比例明确绑定分子和分母，多行取值明确`row`或`aggregate: "sum"`，KPI不默认取末行。错误只修绑定/公式，沿用两轮修订和到限部分交付；业务解释不增加模型复审。字段见schema.md的「正文数字绑定」。

## 按需读取

| 需要 | 文档 |
|---|---|
| spec 全字段、证据/SQL/内联来源 | [schema.md](reference/schema.md) |
| 结论、数字、建议与局限的写作约束 | [quality.md](reference/quality.md) |
| 选择 `answer/brief/analysis` 页面或 `attribution/compare/trend/table/chart` 章节 | [blocks.md](reference/blocks.md) |
| 自定义主题与组件 | [themes.md](reference/themes.md) |

可唯一确定的证据关联和源结果局限由服务补充；多候选时仅补具体关联字段，详见schema.md。
