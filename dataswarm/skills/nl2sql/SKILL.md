---
name: nl2sql
description: 将当前工作空间的纯取数请求转为只读 SQL，返回结果、CSV 和预览；支持仅生成 SQL。新的解释或诊断由 dw-analysis 统筹。
---

# nl2sql

输入用户原话 `query`；可选 `catalog/schema` 只影响选表偏好，不改变执行库。先用 `skill_tool(skill_name="nl2sql")` 取得 `skill_directory`。

## 工作判断

- 按完整目标判断：需要新解释或诊断的复合请求交 dw-analysis；纯查询可衔接已有数据的明确计算或排版。缺指标覆盖、缺输入或查询失败不自动升级分析。
- 核对召回的发布指标定义、维度与时间范围，适配部分交 dw-metrics，未覆盖部分才写 SQL，并保留强制口径。候选命中不等于适配，也不将自定义口径称为权威指标。
- 依据用户条件、术语定义、已确认映射与实际表列选择查询；歧义结合完整上下文判断，影响答案且仍无法合理选择时澄清。采样只补未决条件，样本漏值不证明源中不存在该值，不据此改写用户条件。
- 召回的 field_context 是已采集画像，按其源、快照、采样范围与完整性使用；缺项如实说明，不查内部画像元表或其他会话产物，也不将临时聚合冒充已采集画像。

## 执行与交付

执行前读 [query-workflow.md](references/query-workflow.md) 的召回、预检和导出契约；采样、补结构、关联探查和错误恢复仅在对应条件触发时读取执行。只生成 SQL 时静态校验后交付，跳过执行计划、业务查询和导出；DDL 源只解释结构或生成目标方言 SQL。

方言以返回的 `execution_environment.sql_dialect` 为准，SQL-only 用 `target_sql_dialect`。需要核对函数或类型差异时读相应 [Doris/MySQL](references/mysql-doris.md)、[DuckDB](references/duckdb.md) 或 [SQLite](references/sqlite.md)，不预读所有方言。

保留完整结构化结果和 CSV，用已有预览展示；区分输出 truncated 与输入 data_scope.complete，披露限制，不能用部分数据声称全量总额、全局排名或覆盖率，也不靠重复查询拼接冒充全量。失败按返回 hint/retry/stage 修正，不原样反复重试。

全部访问走受控脚本与当前工作空间注入，不覆盖 JIUWENSWARM_WORKSPACE_ID，不直连或改连接绕过权限、撤销、只读和强制规则。确认当前源、命令传参和缺配置处理见操作手册；只有排查连接配置时才读 dw-db-connector。
