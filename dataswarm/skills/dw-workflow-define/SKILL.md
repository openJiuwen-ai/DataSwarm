---
name: dw-workflow-define
description: 工作流定义原子能力。当用户提到"创建工作流/建工作流/改工作流/删工作流/查工作流/找类似工作流/查同名工作流/工作流查重/模糊搜工作流/工作流节点/DAG/配置节点/工作流结构/任务编排成工作流"时使用。只管工作流定义和节点 DAG 结构的增删改查，不碰上线/调度（见 dw-task-schedule）、不执行（见 dw-task-execute）。通过 `dwcli workflow` 调用。
allowed_tools: [bash, read_file]
---

# dw-workflow-define · 工作流定义原子能力

只管**工作流定义**和**节点 DAG 结构**。工作流由多个任务节点组成，节点带前驱关系构成 DAG，可设等待审核节点。

> **API 化说明（方案 docs/skill-cli-web-api-plan.md 批次 2）**：本 skill 全部数据访问经
> `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，不再直连数据库。**要求 web 服务常驻**；服务不可达时 dwcli
> 退出码 2 并提示连接失败。

## 何时触发
- 用户要**创建/修改/删除/查询工作流**或其节点结构
- 用户说"建个 GMV 巡检工作流"、"给工作流加个节点"、"配置节点的前驱"、"工作流节点列表"

## 何时不触发
- 任务本身的定义 → `dw-task-define`（工作流节点引用 task_id，任务要先用那个建）
- 上线/下线/暂停/审核 → `dw-task-schedule`
- 调试工作流/看实例 → `dw-task-execute`

## CLI

```bash
# 只读
dwcli workflow list [--status draft|published] [--q "关键词"] [--limit N] [--offset N]
dwcli workflow get --id <workflow_id>          # 响应已含 nodes，原 nodes action 合并到此

# 写操作（先预览、后确认、再执行）
dwcli workflow create --name "..." [--description "..."] \
    [--schedule-type none|once|cron --schedule-cron "..." --schedule-once-at "..."]
dwcli workflow update --id <workflow_id> [--name ...] [--description ...] [--schedule-type ... --schedule-cron ...]
dwcli workflow delete --id <workflow_id>

# 整体替换节点（nodes 数组走文件，§6.1 结构化通道）
dwcli workflow nodes --id <workflow_id> --json-file nodes.json
```

> `dwcli` = `python -m jiuwenswarm.cli.dwcli`，下同。`--json-file` 也支持 `-`（从 stdin 读）。

## nodes 的节点 JSON 字段
- `task_id`（必填，引用 dw-task-define 建的任务）
- `node_name`（节点显示名）
- `group_name`（组名，组内节点在前端合并展示）
- `upstream_nodes`（逗号分隔的 node_id 前驱，构造 DAG）
- `wait_review`（true=到该节点暂停等人审核）
- `order_index`（排序）

nodes 是**整体替换**（覆盖该工作流所有节点）。**同批提交可自定义 `node_id`（如 `n_ddl_a`），后续节点的 `upstream_nodes` 直接引用同批里已声明的 `node_id`，无需先创建再回填**；不传 node_id 则自动分配。因 nodes 是结构化数组，必须经 `--json-file`（文件路径或 `-` 从 stdin 读），不支持内联 JSON。

**文件格式**：`--json-file` 指向的 JSON 文件**顶层必须是对象** `{"nodes": [...]}`（服务端按此键取节点数组），不是裸数组。`--json-file` 后直接跟文件路径，**不加 `@` 前缀**（`@` 前缀只用于 `--code @file.sql` 等标量 body 参数，是另一套机制）。

```bash
# nodes.json 内容示例（顶层是 {"nodes":[...]} 对象）：
# 同批内自定义 node_id，upstream_nodes 直接引用同批已声明的 node_id（无需先创建再回填）
{"nodes": [
  {"node_id":"n_ddl_a","task_id":"bld_ddl_a","node_name":"建ODS表A","upstream_nodes":""},
  {"node_id":"n_etl_a","task_id":"bld_etl_a","node_name":"ETL-DWD","upstream_nodes":"n_ddl_a"}
]}

# 也可从 stdin 传入：
echo '{"nodes":[...]}' | dwcli workflow nodes --id <wf_id> --json-file -
```

## 周期调度参数（创建周期工作流只需这一节，不必读源码）

工作流支持的调度参数与任务完全一致，详见下表。格式细节同 dw-task-define（此处复述，免跳转）：

### 三种调度类型（`--schedule-type`）
| 值 | 含义 | 必带参数 | 触发时机 |
|---|---|---|---|
| `none` | 不调度（默认） | 无 | 只能手动 debug 触发 |
| `cron` | 周期定时 | `--schedule-cron` | 到每个 cron 周期点自动触发整个 DAG |
| `once` | 一次性定时 | `--schedule-once-at` | 到指定时刻触发一次整个 DAG，触发后自动置 none |

### cron 表达式格式
- **5 位标准 cron**：`分 时 日 月 周`，如 `0 2 * * *` = 每天 02:00。**推荐用 5 位**。
- 也支持 7 位 Quartz：`秒 分 时 日 月 周 年`。
- **时区固定 Asia/Shanghai**，不能在 cron 里指定时区。
- 校验失败时调度器**静默跳过**（不报错、不触发），cron 写错的唯一表现是"工作流从不自动执行"。
- 常用：`0 2 * * *` 每天 02:00｜`0 */2 * * *` 每 2 小时｜`0 9 * * 1-5` 工作日 09:00

### once-at 时间格式
- ISO8601：`2026-08-29T02:00:00`（不带时区按 Asia/Shanghai）或 `2026-08-29T02:00:00+08:00`。

### 端到端：建一个每天 02:00 跑的分层 DAG 工作流并上线（三步，不必读别的文件）
```bash
# 1. 建工作流并设 cron（status=draft，调度器不扫）
dwcli workflow create \
    --name "每日数仓分层ETL" --schedule-type cron --schedule-cron "0 2 * * *"

# 2. 配节点 DAG（task_id 引用 dw-task-define 建好的任务；upstream_nodes 构造分层依赖）
#    nodes 是结构化数组，写 JSON 文件（顶层 {"nodes":[...]} 对象）再传路径
dwcli workflow nodes --id <wf_id> --json-file nodes.json
#   nodes.json 内容示例（同批内自定义 node_id，upstream_nodes 直接引用同批已声明的 node_id）：
#   {"nodes": [
#     {"node_id":"n_ddl_a","task_id":"bld_ddl_a","node_name":"建ODS表A","upstream_nodes":""},
#     {"node_id":"n_etl_a","task_id":"bld_etl_a","node_name":"ETL-DWD","upstream_nodes":"n_ddl_a"}
#   ]}

# 3. 上线（publish 后才被调度器扫描，到 02:00 自动触发整个 DAG）
dwcli workflow publish --id <wf_id>
```
注意：只设 `--schedule-type/--schedule-cron` 不 publish，调度器不扫描。nodes 是整体替换，重复调用会覆盖之前的节点。

## 写操作预览确认协议
对 create/update/delete/nodes：先展示预览 → 等用户"确认" → 执行。get/list 只读无需确认。

## 输出
path-mode JSON：`{workflow_id, name, status}` 或 `{nodes:[...]}`。上层编排用 workflow_id 衔接（如调 `dwcli workflow publish` 上线、`dwcli workflow debug` 调试）。
