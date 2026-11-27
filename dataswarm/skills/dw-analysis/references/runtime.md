# 分析工具契约

业务规划与判断遵循 SKILL.md；本页按 action 查参数。公共取数、父计算与原始传输仅在使用对应能力时读取。

调用 `analysis_task(action="...", payload={...})`。除 start 外沿用返回的 analysis_id。需要 attempt_id 的操作：重传同一输入沿用 ID，修改输入换新 ID。会话自动绑定空间与 lease；fetch类操作涉及多个活动任务时显式填task_id，dispatch由服务选择任务，不传task_id或任务列表。

## start / context

start 只登记原问题，公共语义成功返回后，在首次 submit-plan 一并提交完整 intent 和计划。意图只保存一次，后续不支持修改。

start 顶层只传 question_raw、可选 session_id/analysis_effort/limits；会话分析强度由用户选择。analysis_scope 属于首次规划的 intent。

```json
{"question_raw":"比较各地区本季度销售额并解释变化"}
```

首次 submit-plan 的 intent 字段如下；question_raw 必须沿用 start 原话。意图与计划原子提交，失败后可修正首次输入，成功后冻结。后续计划省略 intent；重传相同值可接受，修改会被拒绝。

| intent 字段 | 用法 |
|---|---|
| question_raw / question_std / intent / goals | 原话、标准问题、意图和业务目标；goals 按顺序编号 g1、g2 |
| conditions / unknowns | 用户条件对象、未知项字符串数组 |
| mentions | 可选；每项 text/role，role 为 measure、dimension、filter、time 或 subject |
| defaults | 可选；字符串键值对象数组，如 `[{"assumption":"按自然月分组","basis":"用户指定季度"}]` |
| optional_goal_ids | 可选补充目标的 g 序号；不把用户要求降为可选 |
| goal_basis | 按g序号提供origin=user/necessary/exploration与reason；用户依据或必要性说明，exploration目标不进入必答覆盖 |
| analysis_scope | focused 或 overview，按问题范围选择 |

可选 entry_decision 记录 answer_goal/evidence_text/remaining_work/input_basis；evidence_text 来自用户请求。外部文件尚未接入完整分析证据链时，可做独立文件计算，不擅自改为查库。

会话 start 自动取公共 context；失败后用 `context` 重试，payload 为 analysis_id、attempt_id，可加 source_ids 限定已有源。返回路由 term_shortcut/full_recall/full_context 分别为术语定向、完整召回和小目录直读；no_metric_candidate 仅表示指标未命中。

## submit-plan / dispatch

submit-plan 传 analysis_id、tasks；首次必须同时提交 intent，后续沿用已保存意图。可加 perspectives、assumptions、data_products、summary。失败按字段错误修正；成功后会话自动调度就绪任务。

```json
{"analysis_id":"返回的ID","intent":{"question_raw":"比较各地区本季度销售额并解释变化","question_std":"按适用业务定义比较地区销售额并解释变化","intent":"analysis","goals":["比较地区销售额","解释变化"]},"tasks":[{"task_id":"region","query_text":"核实地区销售额差异及变化线索","purpose":"比较地区表现并解释变化","goal_ids":["g1","g2"],"expected_output":"可比较的地区销售额、变化线索及数据缺口"}]}
```

任务字段：

- 必填 task_id、query_text、purpose、goal_ids、expected_output。
- required_outputs可选：`[{output_id,description,checks:[{kind,field,values,descending,target_output,target_field,tolerance}]}]`。field为业务结果的逻辑角色，不是物理列。kind可选nonempty、coverage、unique、ordered、sum_equals；coverage指定用户明确要求的values，sum_equals引用另一个必要产物的逻辑角色。仅按真实交付需要声明，不把所有检查当必做。
- acceptance为业务完成条件，缺省沿用expected_output；required_outputs的逻辑字段由worker在完成时绑定实际证据列。回包acceptance_result记录性质验收，needs_parent_decision表示主管需审阅；完成绑定协议见fetch-actions。
- 可选 source_id（单候选可省略）、perspective_ids、constraints、material_refs、assumption_ids、depends_on、required、required_inputs、supersedes、scope（core/exploration）、requirement_basis（必要性或探索对判断的价值）。
- 执行行数上限不是业务 TopN，业务排名范围在任务问题中明确。

perspectives 每项含 perspective_id/question/goal_ids/required/rationale，任务用 perspective_ids 关联。一份结果可支持多个问题。可逐步补齐 unplanned_goals；同次提交可修订未执行草案和新增任务。已执行任务不能原位改写。

perspectives也可填scope=exploration。探索方向不成为必答门槛。首轮必要任务返回后调用review-core：`{analysis_id,core_review:{task_ids:[全部当前必要core任务ID],answer:{summary,evidence_refs,remaining_questions}}}`。答案引用有效core证据；必要任务仍运行、缺任务或假引用会拒绝。无必要缺口且core完整时返回planning_stage=supplementary，允许规划探索；有缺口只允许fills_gap逐字引用该缺口的core补证或partial交付。新增补证后必须重新综合所有必要core任务。current_answer仅保存解释，不能解锁探索；closing拒绝review-core及任何新增取数。

分析强度由用户界面选择，start回传analysis_effort、limits、behavior_policy_version及behavior_policy（version/rules）。context回包同样带该分析版本的规则，供恢复或原生客户端读取；旧分析空版本沿用旧策略。行为规则见[分析强度行为策略](effort-v1.md)。resources提供档位、行为版本与剩余额度。查询尝试包含拒绝/失败后的重新提交，已有结果复用不消耗；SQL实际执行另行统计。delivery_checkpoint停止新增取数，使用已有结果完成任务或报告，不重开分析重置额度。

assumptions 每项含 decision_id、issue、candidates、selected（逐字取其中一项）、basis_refs、reason、affected_tasks、impact、unverified=true。assumption_ids 只能引用作用范围包含本任务的决策；关联任务执行后使用新 decision_id 记录变更。

替代失败任务时用 supersedes 保留原目标、口径、范围与历史；安全或预算失败不允许替代。新需求另行规划。

最多3个子任务并行，任务总数以start返回的limits.max_tasks为准，不要求达到上限。dispatch传analysis_id选择必要的就绪任务。可选任务默认不自动执行，确有业务必要时传include_optional=true继续；必要消费者的公共生产者仍自动执行。fetch_results含results、completion与findings；普通依赖可使用上游有效partial结果继续，required_inputs仍要求完整公共产物。reused_tasks为任务别名，后续沿用已有ID。

会话执行器持有在途worker，最先完成的任务可先返回fetch_results，pending_task_ids标识仍在运行或等待收取的任务。对pending任务用fetch-run收取结果，不重新prepare或派发。finish若仍有在途任务先转closing并收集成果；全部返回后再校验报告。原生HTTP fetch-run仍是同步单任务执行，不等同会话批次收集。

phase为exploring/closing/delivered，closing_reason记录停止探索原因。closing拒绝submit-plan、dispatch、context、fetch-prepare与新SQL/探查，不可改回exploring；活动任务可fetch-run整理已有证据，继续inspect/reuse/compute/complete_task。收尾复用对话超时，不另设报告额度，所有实际usage继续记账。

### 公共取数

data_products 每项：product_id、producer_task_id、source_id、requirements（范围、粒度、口径与完成标准文字）；可选 version（默认1）、required_columns、require_complete（默认true）。生产任务放入 tasks，消费者填：

```json
{"required_inputs":[{"product_id":"common_data","version":1}]}
```

服务自动生成 depends_on。生产者 complete_task 时用 output_bindings 指定证据，详见 [取数动作](fetch-actions.md#完成与空结果)。生产任务完整完成并固定输出后，消费者收到 input_bindings，直接使用其中已属于本任务的 evidence_id；无需搜索目录或重新取数。无依赖的任务可独立并发。输入不足时保留缺口，增量规划公共补取。

## read-results

批量读取已有证据文件：`{analysis_id,read_requests:[{evidence_id,columns?,offset?,limit?}]}`。无需attempt_id，不执行SQL、不生成新证据；columns缺省为全部列，offset从0开始，limit默认20、最大5000行，每批最多16份。只按行分页，字段内容完整返回；有has_more时按next_offset续读。

## fetch-run / status / finish

父Agent整理已有证据可调用`compute-results`，payload为`{analysis_id,attempt_id,proposal}`；proposal为compute动作（algorithm/params或code二选一、evidence_refs、reason，可选row_limit）。无需task_id/lease，不重开已完成子任务；输入须为当前分析有效同源证据，产物独立保存到computations并可继续计算或报告引用。相同attempt重放沿用结果，修改参数换ID。closing仅处理已有证据，delivered后不能新计算；参数错误只修计算，不新增查询。独立CSV计算使用data-insight CLI。

interrupted 或返回 resume_task 时，按 next_action 用 fetch-run（analysis_id/task_id）续跑；仍在执行的任务不重复启动。revise_plan 时修未执行草案。查询状态不确定时保留说明，不重发相同 SQL。预算以 status 返回的 limits/usage 为准。

status只读。finish传analysis_id、summary，以及report_spec或report_spec_path（二选一）；spec字段、证据关联及图表数据契约由dw-report维护。

报告关联失败时服务累计failed_submissions，最多允许两轮模型修订，第三次仍失败则逐条隔离未核验内容并交付partial报告。能唯一还原的显式data_evidence_ref或图表绑定由服务补齐；不猜文字结论引用。待核验部分保留为明确草稿，不呈现无法验证的图表或正式KPI，不计已回答目标；真实权限、版本、哈希及因果违规不因达到次数而放行。修订仅改关联，不重新查询，不新建分析或换attempt重置次数。

长 spec 用 Python 字典和 json.dump 写文件；report_spec_path 须位于本分析 artifact 目录内，可直接finish，无需额外CLI预检。模型认为仍有真实业务缺口时在report_spec中声明status=partial及说明，不向finish的payload顶层添加status；必要任务未完整完成或有明确剩余问题时服务自动标partial。仅缺目标关联记录unconfirmed_goals，不要求重取数据或重提报告。服务返回 missing_goals、unfinished_tasks、evidence、assumptions、manifest_path 及实际产物/发送状态；失效证据不用于交付。

## 原始传输

会话工具自动完成 start → context、submit-plan/dispatch → worker 和 finish 渲染。CLI 是手动原始传输，不自动串联：

```bash
python "{skill_directory}/scripts/analysis_flow.py" <action> --input-file payload.json
```

action 可用 start/context/submit-plan/review-core/dispatch/fetch-prepare/fetch-step/fetch-run/status/finish。HTTP/CLI 的 fetch-prepare/fetch-step/fetch-run 显式携带 task_id、lease；fetch-prepare/fetch-step 另需 attempt_id。fetch-step 的 proposal 动作与计算参数见 [fetch-actions.md](fetch-actions.md)。CLI finish 仅汇总，渲染使用 dw-report。

验收或完成绑定需手动修订时，按fetch-actions的complete_task协议处理。执行器负责空等待，不占主管模型轮次；最新工具反馈保留完成状态和业务总结。
