---
name: dw-analysis
description: 统筹需要形成新解释、原因诊断、综合评估或开放探索的数据分析；纯取数、明确计算和已有结论排版使用对应技能。
version: 3.11.0
allowed_tools: [analysis_task, read_file, write_file, bash, code, send_file_to_user, skill_tool, skill_index, tool_search, tool_call]
---

# dw-analysis

用会话绑定的 `analysis_task(action, payload)` 推进分析。主 Agent 负责业务规划、综合解释和交付；隔离取证 worker 自主选择表列、SQL、指标参数与计算方法。

## 业务流程

1. **明确目标**：start 只登记 question_raw 用户原话，先取得公共语义；结合原问题、已有对话和召回材料，在首次 submit-plan 一并提交 intent 与计划，成功后不支持修改意图。目标、条件与未知项区分用户要求、必要支撑和可选探索。标准化问题不能扩写用户条件；自行选择的比较基准记为方法或假设。沿用已有分析身份，召回失败按反馈恢复，不重开分析重置预算。
2. **规划核心回答**：首批 scope=core 只包含正确回答所需的工作。先尝试用一个完整调查任务覆盖当前问题；一个 worker 可在任务内多轮查询、计算、验证并返回多份证据。intent.goals 是答案覆盖要求，任务可通过 goal_ids 同时覆盖多个目标，不逐项目标创建任务。围绕同一判断的基线核实、异常识别、对象定位与证据验证，若需要共用中间结果或统一判定口径，放在同一任务内完成，不按方法、步骤或报告章节拆开。
   只有存在独立交付边界且拆分有明确收益时才增加任务，并在各任务 requirement_basis 中说明独立产出、拆分收益及结果如何衔接；目标不同或可以分别写一段结论，本身不足以证明应拆分。拆开后若需重复生成同一特征或候选集合，优先合并；确需拆分则用 depends_on 或公共产物声明复用关系。互不依赖且工作量较大的不同问题仍可分开；共用表、时间或报告不要求合并，也不为合并增加关联分析。任务数与并发是上限，不是目标；收到结果后只针对具体必要缺口补证。
   任务写业务产出与 acceptance，不预写物理表列或算法；required_outputs 只声明必要的结果性质，不为排版指定表形。未由用户指定且无现有证据支持的额外模式、窗口组合或候选数量，不预设为核心验收要求，由 worker 按取证需要选择方法。最终分析报告至少用一张图支撑核心判断；可选折线图（趋势）、柱状图（比较）、饼图（构成）、散点图（关系）、热力图（二维分布）。规划时在相关任务 expected_output 中说明需保留的比较关系、粒度与单位，随必要取证返回可引用数据，具体呈现交付时加载 dw-report，不为出图扩展取数。
3. **综合与继续**：用已返回证据形成答案并审阅解释是否充分。全部必要 core 任务返回后，需要继续工作时以 review-core 保存综合答案；具体门禁与字段见 runtime。必要缺口留作补证或 partial；新方向由主管按已有线索和预期价值选择 scope=exploration，不能藏进 core 验收条件。子任务返回的 followup_questions 是候选，不自动成为必答目标；验收失败先审阅已有成果，不自动循环补查。
4. **交付**：按当前 acceptance 与所选分析强度判断验证是否充分，再决定 finish 或继续。remaining_questions 只列必要缺口，可选探索不阻塞交付。closing 后只整理已有证据，不新增取证；预算停止本身不决定 completed/partial。主管统一口径、结论与局限，使用 dw-report 正常流程生成并核实交付；组装 report_spec 前对照回执 question_anchor 与 acceptance_ledger 判断是否回答原问题并说明真实缺口，验收项与任务完成说明无需逐字抄入正文；报告或发送失败须保留证据并说明实际状态。

## 判断与边界

- 按 start/context 的 behavior_policy 和 analysis_effort 执行；验证投入与任务必要性分别判断，预算是上限，不以查询、维度或图表数量衡量深度。已收到同版本规则时不重复读文件，恢复缺失时获取原版本规则，不自行改档或升级。
- 保留用户目标、范围、禁止事项与强制规则。主结果满足要求的口径、粒度与时间；辅助对照说明用途。没有影响判断的具体风险，不把所有潜在维度和核验都列为必要工作。
- 候选定义和 planning_adopted 不等于用户确认；按业务含义选择指标，自定义口径说明差异，只有 authoritative=true 可称发布指标编译结果。结合语义识别影响答案的时间、分母、归属与单位歧义，必要未知项记入 intent.unknowns，采用的假设在 assumptions 说明依据与影响；影响答案且无法合理选择时澄清，不把未验证选择当作用户确认。
- 按真实粒度与可加性计算；区分事实、贡献、相关性和因果，未验证解释保留为假设。结构验收只证明声明的结果性质，不证明业务口径或解释正确。
- 取数与证据计算走 analysis_task。已有证据足够时直接综合，缺派生结果用 compute-results 登记，不为解释、排版或文件发送另建取数任务。本地文件不自动成为证据；权限、撤销、只读和强制规则错误不能换工具绕过。

## 按需读取

| 需要 | 文档 |
|---|---|
| start、规划、review-core、恢复、计算或 finish 调用 | [runtime.md](references/runtime.md) 对应章节；公共取数与原始传输仅在使用时读取 |
| 入口仍不明确 | [intent-routing.md](references/intent-routing.md) |
| 选择诊断方法 | [insight-path.md](references/insight-path.md) |
| 缺失 effort-behavior-v1 规则回包 | [effort-v1.md](references/effort-v1.md)；其他版本获取对应规则 |
| 手动 fetch-step 或自定义计算协议 | [fetch-actions.md](references/fetch-actions.md)；正常 worker 执行无需主管读取 |
| 报告 spec 与呈现 | 加载 dw-report，按其指引读取 schema 与质量说明 |
