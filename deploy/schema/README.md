# deploy/schema — 系统元数据表唯一真相源

## 约定

- **每张表两份方言、各一个文件**：`ddl/<table>.ddl.sqlite.sql` 与
  `ddl/<table>.ddl.mysql.sql`。每个文件只含一条 `CREATE TABLE IF NOT EXISTS`
  （保证 ensure 幂等可整目录跑）。doris 方言已于 2026-10-24 退役，目录内不再存在
  `.ddl.doris.sql`；两份方言必须同时修改。
- **全字段首装即终态**：MVP 不做版本化迁移。改结构 = 同步修改双方言文件 + 对存量库手工 ALTER，并在下方「升级记录」追加一行。
- 命名保留 `dw_` 前缀（历史决策）；`column`/`table` 等关键字列名在 SQLite/MySQL 访问时一律走 `db_connector.quote_ident` 引号包裹。
- JSON 类字段统一存文本：SQLite 用 TEXT，MySQL 用 LONGTEXT，存 canonical JSON（`_dw_common/scripts/digest.canonical_json`）。
- 已退役的空表 `dw_column_lineage` 不在真相源内（已于 2026-08-27 从存量库删除）。
- 当前共 **36 张表**（编排、建仓、BI 看板、指标语义、业务知识、语义采集与巡检等），
  以 `ddl/` 目录为准：同名 `.ddl.sqlite.sql` + `.ddl.mysql.sql` 各一份，缺一即门禁报错。
  新增表直接在 `ddl/` 落两份方言即可，`ensure_system_schema` 会幂等建表。
- 运行期逻辑不迁移：血缘 writer 的 `task_id` 幂等加列、bootstrap 的 `reuse_of_plan_id`
  加列与 `_discovery` 去重仍保留兜底，对首装即终态的库是 no-op。
- SQLite 索引单独成文件：`idx_*.ddl.sqlite.sql` 与表 DDL 同目录扫描，由
  `ensure_system_schema` 一并幂等应用（SQLite `execute` 不支持多语句，索引不能与
  CREATE TABLE 同文件）。MySQL 侧索引直接写在各表的 `.ddl.mysql.sql` 内（`KEY ...`），
  不另设索引文件。

## ensure 链路

解析顺序（与 `jiuwenswarm/channels/web/schema/ensure.py` 实际实现一致，共四级）：
1. env `JIUWENSWARM_DEPLOY_SCHEMA_DIR`
2. `<数据根>/deploy-schema/`（`scripts/dw-ops/sync_schema.py` 刷新的副本；数据根经 `get_user_workspace_dir` 解析，`JIUWENSWARM_DATA_DIR` 优先，DataSwarm 入口默认 `~/.dataswarm`，无 env 回退 `~/.jiuwenswarm`）
3. 本仓库 `deploy/schema/`（沿 `ensure.py` 的 `__file__` 上溯，开发模式零配置）
4. 包内 `jiuwenswarm/resources/deploy-schema/`（whl 形态兜底：构建时 `build_dataswarm_dist.py` 把本目录注入包内，前三级皆空时命中此级）

修改本目录后执行 `python scripts/dw-ops/sync_schema.py` 刷新运行目录副本。
调用方（三处同一幂等入口）：
- `app_web.main` 启动时同步调 `ensure_system_schema(META_DB_REF)` 兜底，保证 web 监听就绪前全部系统表建好
- `orchestration.bootstrap.start` 起 daemon 线程异步跑 `orch_db.init_db` → `ensure_system_schema`（含遗留清理 + 孤儿回扫）
- `run_pipeline.py` 开头兜底、`fix.py` plan/execute 前

## 升级记录（手工 ALTER 留痕）

| 日期 | 表 | 变更 | 说明 |
|---|---|---|---|
| 2026-08-27 | 全部 | 首装 v1（9 表） | 含 inspection 三表与未来字段列（首装即终态） |
| 2026-08-27 | plan / plan_table / sync_task / table_lineage / column_lineage | 收编 builder 五表（v1.1，共 14 表） | 原 bootstrap_bigdata.py 独建；含终态列 task_id / reuse_of_plan_id；同日删退役空表 dw_column_lineage |
| 2026-08-31 | dw_bi_metrics / dw_bi_dimensions / dw_bi_dashboards / dw_bi_dashboard_cards | 收编 BI 4 表（v1.2，共 18 表） | 原 dashboard/db.py 内嵌；4 条 SQLite 索引单独成 `idx_bi_*.ddl.sqlite.sql` 文件；plan 终态列 purpose/content_json/plan_file_path 补入首装 DDL，sqlite_store/doris_store 的 ensure_columns 兜底删除；删 `dataagent/bootstrap.py` + `dataagent/db.init_db` + `MetadataStore.init_schema` 抽象方法，schema 初始化统一收敛到 `app_web.main` 同步调 `ensure_system_schema`；**存量 v1.1 库需手工 ALTER plan ADD COLUMN 3 列** |
| 2026-09-01 | dw_inspection_findings | 加 llm_verdict/llm_reason 两列（v1.2） | LLM 语义复核留痕；运行期幂等 ALTER，首装库 no-op |
| 2026-09-02 | sync_task | 删除表（v1.3，共 17 表） | Step 3 `generate_sync_tasks.py` 已废弃，下游无消费方（plan_to_tasks 直读 plan_table.source_tables）；删表 + 删 `generate_sync_tasks.py` + 删 store 三方法；**存量库需手工 DROP TABLE sync_task** |
| 2026-09-16 | dw_bi_metrics | 加 metric_type/expr/synonyms/time_grain/dimension_ids/verified_sql_json 6 列（v1.4） | 指标语义层（bi-metrics B1）；**存量库需手工 ALTER ADD COLUMN 6 列**；同批新增 dw_bi_metric_bindings / dw_metric_queries 两表（首装 DDL 已含，存量库由 ensure_system_schema 幂等补建） |
| 2026-09-16 | dw_datasources | 加 description 列（备注，v1.4） | 数据源用途备注；双方言 DDL 已更新；存量库由 `datasource/store._ensure_table` 运行期幂等 ALTER 自动加列（首装库 no-op），无需手工 ALTER |
| 2026-09-16 | table_lineage / column_lineage | 加 business_ref 列（血缘跨数据源隔离，v1.5） | 写入时记录归属业务数据源规范化 ref；查询侧按当前绑定 ref 严格过滤（NULL 存量行不显示，任务重新 publish/执行后重建）；双方言 DDL 已更新；存量库由 `lineage/writer.ensure_schema` 运行期幂等 ALTER 自动加列（首装库 no-op），无需手工 ALTER |
| 2026-10-08 | dw_metric_defs / dw_metric_revisions / dw_metric_bindings / dw_metric_releases / dw_metric_concept_map / dw_metric_query_log | 新增指标语义层六表（v1.6） | **按「元数据库 Doris 已弃用」决策，本批起新表 DDL 写 sqlite + mysql 双方言，不再写 doris**——mysql 文件须等 `ensure.py` 的 `_DIALECT_SUFFIX` 扩展后才被执行（阶段 0 前置）；`dw_metric_verified_sql`（经确认 SQL 复用池）留待 LLM 层提交一并加 |
