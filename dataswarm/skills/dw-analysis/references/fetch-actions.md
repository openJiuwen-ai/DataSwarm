# 取数、复用与计算动作

正常会话由隔离 worker 执行；仅手动取数、计算或排查时读取本页。fetch-prepare 返回 task/materials/assumptions。fetch-step 的 payload 含 analysis_id、task_id、attempt_id、proposal；会话自动解析 lease，HTTP/CLI 显式提供。

business_basis 区分用户要求、发布定义、协调者建议与假设；planning_adopted 仅表示规划采用。assumptions 的 affected_tasks 只能包含当前 task_id。

## 查询与补资料

inspect_result读取已有完整CSV，可仅传evidence_refs批量读取；需要选列或分页时传read_requests，格式及边界见[runtime.md的read-results](runtime.md#read-results)，服务按证据ID定位文件。查询回包预览仍有界；显式读取返回next_offset，不因预览截断重查数据库。同分析同源的有效跨任务证据可直接用于complete_task引用和绑定，服务验证文件并自动登记复用血缘。partial或校验失败保留已有业务结论与发现，引用错误单独返回。

| proposal.action | 参数 |
|---|---|
| propose_query | sql：来自授权材料的只读 SQL，可跨表；不传 query_spec 或客户端 temp_scope |
| compile_metric | metric_query：selected_metrics、dimensions、time、filters、order_by、top_n、row_limit，按已召回发布定义填写 |
| request_evidence | tool、arguments、missing：缺少的具体字段、值或关联；reason 不能代替 missing |
| cannot_execute | reason：无可执行合法解释的原因 |
| describe_compute | algorithm：计算能力目录中的算子名、custom_code或aggregate/distribution helper名；只读说明，其他执行字段不填 |

metric_query.time 用 dimension/start/end_exclusive/grain；top_n 同时指定 order_by。row_limit 默认500、上限5000；截断时调整查询或明确提高上限，不把部分结果当全量。

```json
{"analysis_id":"返回的ID","task_id":"region","attempt_id":"region-query-1","proposal":{"action":"propose_query","sql":"SELECT region, SUM(amount) AS total FROM orders GROUP BY region"}}
```

补资料 tool 参数：describe 用 table；probe 用 table/column/like?/limit?；join 用 left/right（各含 table/column）及 sample?；list_tables 无需表名。以上 tool/arguments/missing 均放 proposal 内。

## 已有结果与计算

result_catalog 是同分析同源结果目录；按范围、粒度、口径和完整性判断是否适用。公共输入 input_bindings 已指定可用证据，无需再搜索或复制。

| proposal.action | 参数与输出 |
|---|---|
| inspect_result | evidence_refs；返回列、行数与最多5行样例行，preview_only=true 表示未返回全部行；查看完整数据用 compute |
| reuse_result | evidence_refs、reason；登记为本任务证据 |
| compute | evidence_refs声明输入，reason说明方法；algorithm调用现有data-insight算法，或code运行自定义Python；params为参数 |

取数前按计算目录确认所需列、粒度与范围；选定方法后可先用`{"action":"describe_compute","algorithm":"formula_breakdown"}`读取详细契约，再准备SQL或CSV。说明按任务保留在computation_docs，跨轮次和续跑可见，不需重复读取；它不执行SQL、计算或消耗取数轮次，但实际模型调用仍记账。closing时可为已有证据计算读取说明，不能据此新增取数。

例如 `{"action":"compute","algorithm":"trend_profile","params":{"time_field":"month","metric_field":"gmv"},"evidence_refs":["实际ID"],"reason":"解释月度变化"}`。多输入时params.input_evidence_ref选起始CSV，table_ops的join.input仅引用声明的其他证据ID。自定义Python及aggregate/distribution helper用describe_compute读取对应契约，仍支持原有compute/code调用，不限制算法方法。

自定义code的inputs始终为`{evidence_id: list[dict[列名,字符串单元格]]}`，用`rows = inputs["实际证据ID"]`读取，包括单输入场景。设置 `output={"columns":[...],"rows":[...]}`，或 `{产物名:{"columns":[...],"rows":[...]}}` 输出多表；也可定义 `run(inputs, params, helpers)` 返回相同结构。失败后pending_computation保留上一版代码、参数和输入，可局部修复。helpers仅为可选aggregate/distribution，算法不设白名单。

计算只处理声明输入及临时产物，不连接数据库、网络或启动子进程；补取数据仍走查询动作。复用和计算保留输入局限与版本血缘，主 Agent 任意本地文件不自动登记证据。

## 完成与空结果

query_succeeded 仅保存证据，任务可继续查询。证据足够时显式完成：

已有事实或解释随任意动作提交 `findings:[{"text":"发现与依据","kind":"fact|interpretation|hypothesis","evidence_refs":["实际ID"]}]`。

```json
{"action":"complete_task","reason":"说明交付标准如何满足","evidence_refs":["本任务实际证据ID"]}
```

尚有缺口时写remaining_questions字符串数组，服务自动将completion_status设为partial，不因重复状态字段不一致退回。主动声明partial即保留；服务不从reason或任务名称猜造新的业务问题。不能用物理补查或未绑定的其他任务证据完成本任务。status 表达执行状态，completion.status 表达业务完成度，quality 表达结果可用性；succeeded 不一定表示业务完整完成。

任务外线索填followup_questions，不计remaining_questions。

若父任务声明required_outputs，完成时附deliverable_bindings。例如父任务要求output_id=monthly、逻辑角色period覆盖两个期间，完成时填`[{"output_id":"monthly","evidence_id":"本任务实际证据ID","fields":{"period":"实际结果期间列"}}]`。脚本核验完整有效CSV，返回acceptance_result；失败时已有成果保留，完成度改partial并needs_parent_decision=true，worker立即返回而非继续补查。父Agent决定追加任务或交付，closing时不能追加。

生产公共输出时同时填 `output_bindings:[{"product_id":"common_data","product_version":1,"evidence_id":"本任务实际证据ID"}]`，并将该证据列入 evidence_refs。绑定所有声明输出，每个版本对应一份表格；partial、缺输出或要求完整但结果截断时不释放消费者。

空结果先核对原条件，再用 `{"action":"confirm_empty","reason":"核验依据","evidence_refs":["实际physical证据ID"]}` 确认。不要删除过滤制造非空，也不把空结果自动当数值零。

复合唯一键用 `{"kind":"unique","fields":["期间","国家"]}`；各逻辑角色在deliverable_bindings.fields沿用相同键并绑定实际列。ordered不符仅warning，不阻断完成；必要覆盖、唯一性或对账失败保留partial。revise_completion只修引用和绑定，禁止新查询；一次修正仍不满足则返回主管审阅。
