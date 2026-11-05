---
name: dw-bi-dashboard
description: BI 看板管理 skill——通过 HTTP API 读写看板（维度 / 看板 / 卡片），支持从 dw-report 产出的 dashboard spec 直接导入固化为持久化看板。严禁直连元数据库写表，所有写入必须走 /api/dataagent/dashboard/ REST API。指标定义/口径请用指标语义层（dw-metrics，/api/dataagent/metrics/v2/）——看板侧不再有独立指标表。
allowed_tools: [bash, read_file]
---

# dw-bi-dashboard —— BI看板管理技能

> **：本 skill 全部数据访问经
> `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，脚本不再直连数据库。**要求 web 服务常驻**；服务不可达时 dwcli
> 退出码 2 并提示连接失败。

## 用途
管理 BI看板的维度定义、看板和卡片。支持从 dw-report 产出直接导入看板。
指标定义/口径管理见 dw-metrics（指标语义层 V2）——卡片 SQL 引用业务表自行取数。

## 何时使用
- 用户要求将数据分析结果/报告固化为持久化看板
- 用户要求创建/编辑/删除维度、看板、卡片
- 用户在 dw-report 生成 dashboard 形状报告后，要求"保存到看板"

## ⚠️ 红线：禁止直接写库

看板数据（`dw_bi_dashboards` / `dw_bi_dimensions` / `dw_bi_dashboard_cards` 三张表；`dw_bi_metrics` 已随旧指标体系退役）**必须**通过 `/api/dataagent/dashboard/` HTTP API 读写，**严禁**：

- 用 `sqlite3` 模块直接连 `~/.dataswarm/metadata.db` 写 INSERT
- 用 `pymysql` 直连 Doris bigdata 写 INSERT
- 用 `code` 工具手写 SQL 语句绕过 API

**原因**：web 后端进程连哪个库由 `~/.dataswarm/config/datasource.yaml` 的 `metadata_db` 字段决定（具体指向随部署环境变化，用 `dwcli datasource current` 查询）。直写本地 SQLite 会让前端读不到（前端读后端 HTTP，后端读元数据库），且库指向迁移后本地副本即失效。

**唯一允许的写入路径**：
1. 调 `dashboard_tool.py` CLI（内部走 HTTP）
2. 直接 curl/Python requests 调 `/api/dataagent/dashboard/` REST API

## 调用方式

### 从 dw-report 导入看板

```bash
python scripts/dashboard_tool.py import-report \
  --spec-path "~/.dataswarm/artifact/analysis/report_<ts>_spec.json" \
  [--dashboard-name "Olist运营看板"] \
  --datasource-ref "doris://<biz_database>"
```

注意：子命令是 `import-report`，**不要**加 `--action` 前缀。

### ⚠️ datasource-ref 必传且必须指向业务库（最常见的看板空白根因）

`datasource_ref` 决定**卡片 SQL 在哪个库执行**（KPI 卡、图表卡、指标 calc_sql、维度 value_sql 都是）：

- ✅ **必传，填业务库 ref**：从 `dwcli datasource current` 返回的 `business_db_ref` 取（当前工作空间绑定的业务库）
- ❌ **留空 = 卡片 SQL 落到系统元数据库**（服务端回退逻辑是 `datasource_ref or META_DB_REF`），业务表在元数据库不存在 → 渲染报错/看板空白。**这是已发生过的事故**，留空不是"用默认业务库"的意思
- ❌ `doris://` 空指针不可靠：需要 config `doris:` 段配了 `database:` 才有值，当前指针形态配置下可能解析不出业务库
- 元数据库 ref（`datasource current` 的 `metadata_db_ref`）只有一种用途：卡片 SQL **刻意查 `dw_*` 元数据表**时才填它。业务数据卡片一律不填这个

导入后验证：`dwcli dashboard cards --dashboard-id <id>`（或 list-cards）检查每张卡的 `datasource_ref` 非空且指向业务库；空了就 update 卡片补上，不要等渲染才发现。

### datasource-ref 取值规范

- `doris://<biz_database>` —— 业务库 ref（从 `dwcli datasource current` 的 `business_db_ref` 取，勿写死库名）。卡片 SQL 跑业务数据**必须**用这个，见上节。
- 元数据库 ref —— **系统元数据库**（从 `dwcli datasource current` 的 `metadata_db_ref` 取），存 `dw_bi_*` 表本身。**不要**把它当业务数据卡片的 `datasource-ref`。
- **不要**写 `doris://biz/olist` 这种自造路径，后端解析它会去 config 找 `biz` 段，找不到就报错或走错库。
- **不要留空**：留空时服务端回退到元数据库（`datasource_ref or META_DB_REF`），业务表查不到，看板空白。

### 通过 HTTP API 操作

所有操作通过 `/api/dataagent/dashboard/` REST API 执行，可使用 curl 或 Python requests：

```bash
# 新建看板
curl -X POST http://127.0.0.1:16173/api/dataagent/dashboard/dashboards \
  -H 'Content-Type: application/json' \
  -d '{"name":"Olist运营看板"}'

# 添加卡片（datasource_ref 必填业务库 ref，sql_query 在该库执行）
curl -X POST http://127.0.0.1:16173/api/dataagent/dashboard/dashboards/<dash_id>/cards \
  -H 'Content-Type: application/json' \
  -d '{"title":"GMV趋势","card_type":"chart","sql_query":"SELECT ...","chart_spec":{"type":"line","x":"month","y":"gmv"},"datasource_ref":"doris://<biz_database>"}'

# 渲染看板
curl -X POST http://127.0.0.1:16173/api/dataagent/dashboard/dashboards/<dash_id>/render
```

## 故障排查：dashboard_tool.py 失败时

**不要**在调用失败后改用 `sqlite3`/`pymysql` 直写库。按以下顺序排查：

1. **验证 web 后端存活**：
   ```bash
   curl -s http://127.0.0.1:16173/api/dataagent/dashboard/dashboards
   # 期望: {"dashboards": [...]} 或 {"dashboards": []}
   # 失败（连接拒绝）→ web 后端没起，先启动它（restart.sh）
   ```
2. **验证子命令语法**：必须是 `import-report`，不是 `--action import-report`
3. **验证 spec_path 存在且是合法 JSON**：
   ```bash
   python -c "import json; print(json.load(open('~/.dataswarm/artifact/analysis/spec_20260828.json'))['title'])"
   ```
4. **看 web 后端日志的报错**（`~/.dataswarm/logs/logs/run/jiuwen.log` 里搜 `dataagent` / `dashboard`），定位是 `import_from_report` 解析报错还是连接 Doris 失败
5. 如果是 web 后端连 Doris 失败，修 `~/.dataswarm/config/datasource.yaml`，**不要**改代码绕过

## 导入流程

1. AI agent 使用 dw-report 生成 `shape=dashboard` 报告
2. dw-report 产出 spec JSON + data JSON 到 `~/.dataswarm/artifact/analysis/`
3. AI agent 调用 `/api/dataagent/dashboard/import-from-report`，传入 spec_path
4. 后端解析 spec，提取每个 section 为卡片，创建看板
5. 用户在侧栏点击"BI看板"查看固化的看板

## ⚠️ 红线：导入看板时禁止改写 SQL

`import-from-report` 接受的 spec 应当是 **dw-report 产出的原 spec**，**不是** agent 重写过的副本。重写 SQL 会引入原 spec 没有的 bug（已发生过事故：agent 为了把 `chart` 字段从 ID 引用改成内联规格，顺手给 SQL 加了 `ROW_NUMBER() OVER(PARTITION BY ...) WHERE rn=1` 子查询，但子查询 SELECT 漏了外层 `GROUP BY` 引用的列，Doris 报 `Unknown column 'xxx' in 'table list' in AGGREGATE clause`）。

### 唯一允许的 SQL 改动：加时间范围参数

看板支持按时间范围动态查询时，需要把原 SQL 的固定 `WHERE dt = '...'` 改成参数化形式（如 `WHERE dt BETWEEN ?start AND ?end`），**这是唯一允许的改写**。其他改动一律禁止：
- ❌ 加子查询（`ROW_NUMBER()`、`LATERAL VIEW` 等）
- ❌ 改聚合维度（`GROUP BY` 的列）
- ❌ 改表名 / 加 JOIN
- ❌ 改列引用（`SELECT` 的列）
- ✅ 把字面量日期换成命名参数（`?start` / `?end`）

### chart 字段格式不匹配时怎么办

dw-report 的 spec 里 `chart` 字段可能是 ID 引用（`"chart": "segment_bar"`，指向 data JSON 的 option）或内联规格（`"chart": {"type": "bar", "x": "...", "y": "..."}`）。两种格式 `import_from_report` 都能处理——**不要**为了统一格式重写 spec。如果原 spec 的 chart 是 ID 引用但找不到对应 data JSON，应当**回到 dw-report 重新产出带内联规格的 spec**，而不是 agent 自己手写一份新 spec。

### KPI 必须带 sql

spec 的 `kpis` 数组每项**必须**含 `sql` 字段，不能只有 `label` + `value`：
```json
// ❌ 错误：只有静态 value，导入后看板无法动态刷新
"kpis": [{"label": "客户总数", "value": "95,420", "unit": "人"}]

// ✅ 正确：带 sql，看板渲染时执行 SQL 取最新值
"kpis": [{"label": "客户总数", "value": "95,420", "unit": "人",
          "sql": "SELECT COUNT(DISTINCT customer_unique_id) AS v FROM dws_user_rfm_1d WHERE dt=(SELECT MAX(dt) FROM dws_user_rfm_1d)"}]
```
缺少 sql 的 KPI 会在导入时被后端拒绝（返 `sql_validation_failed`）。原因：spec 里的 value 是报表生成时的快照，看板是持久化的，应当能随时反映最新数据；只填 value 不填 sql 的 KPI 卡片会显示空白。

KPI SQL 写法规约：
- 返回单行单列，列名建议 `v` 或 `value`
- 用 `ROUND(...)` 控制小数位（GMV 2 位、比率 2 位）
- 取最新日期的快照：`WHERE dt=(SELECT MAX(dt) FROM ...)` 或子查询 `ROW_NUMBER() OVER(PARTITION BY ...) WHERE rn=1`

导入时传 `--datasource-ref "doris://<biz_database>"`（业务库），spec 里所有 SQL 统一落这个库——留空会全部落到元数据库，渲染时空白。

### 验证清单（导入前必跑）

导入前对 spec 里每条 `sql`（含 KPI 和 section）用 `explain_sql.py` 验证一次：
```bash
python {nl2sql_skill_dir}/scripts/explain_sql.py --sql "SELECT ..."
```
任何 SQL EXPLAIN 预检失败都不要导入，回上游修 spec 而不是在导入环节打补丁。后端 `import_from_report` 也会自动跑 `EXPLAIN` 校验，失败返 `sql_validation_failed`，但这不是省去人工验证的理由——后端校验是兜底，不是常态。

## 输出
- 返回创建的看板 JSON（含 dashboard_id, cards, dashboard_url 等）
- CLI stdout 末尾会输出一行 `看板链接: /dataagent/dashboard/dashboards?dashboard_id=xxx`（应用内相对路径），agent 抓取该行路径拼成 markdown 超链接（如 `[看板](/dataagent/dashboard/dashboards?dashboard_id=xxx)`）原样贴到回复里，用户点击直达看板。**禁止**给链接拼接 `http://localhost`、主机名、端口或协议——相对路径由 Chat 前端按当前访问域名自动解析，远程部署也能正确跳转
- 错误时返回 `{error: "..."}`
