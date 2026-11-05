---
name: dw-task-define
description: 任务定义原子能力。当用户提到"创建任务/建一个数据任务/改任务/删任务/查任务列表/找类似任务/查同名任务/任务查重/模糊搜任务/任务定义/任务参数/SQL 任务/Python 任务"时使用。只管任务定义的增删改查，不碰上线/调度（见 dw-task-schedule）、不执行（见 dw-task-execute）、不管工作流（见 dw-workflow-define）。通过 `dwcli task` 调用。
allowed_tools: [bash, read_file]
---

# dw-task-define · 任务定义原子能力

只管**任务定义**的增删改查。每个任务是一个可执行的 SQL 或 Python 单元，带 SLA/分层/前驱/调度参数等元数据。

> **API 化说明（方案 docs/skill-cli-web-api-plan.md 批次 2）**：本 skill 全部数据访问经
> `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，不再直连数据库。**要求 web 服务常驻**；服务不可达时 dwcli
> 退出码 2 并提示连接失败。

## 何时触发
- 用户要**创建/修改/删除/查询任务本身**（任务名/代码/执行器/SLA/分层/前驱）
- 用户说"建个查 GMV 的任务"、"把任务改成查近 7 天"、"删掉这个任务"、"列出所有任务"

## 何时不触发（路由到别的原子 skill）
- 上线/下线/暂停/审核（调度生命周期）→ `dw-task-schedule`
- 调试/执行/看实例/看日志 → `dw-task-execute`
- 工作流定义/节点 DAG → `dw-workflow-define`

## CLI

```bash
# 只读（无需确认，直接执行）
dwcli task list [--status draft|published] [--q "关键词"] [--limit N] [--offset N]
dwcli task get --id <task_id>

# 写操作（先预览、后确认、再执行）
dwcli task create --name "..." --executor-type sql|python --code "..." \
    [--sla-minutes N] [--layer L] [--output-table T] [--datasource-ref D] \
    [--timeout-s S] [--upstream-tasks id1,id2] \
    [--schedule-type none|once|cron --schedule-cron "0 9 * * *" --schedule-once-at "2026-08-24T15:00:00"]
dwcli task update --id <task_id> [--name ...] [--code ...] [--sla-minutes N] [--layer L] [--executor-type sql|python]
dwcli task delete --id <task_id>
```

> `dwcli` = `python -m jiuwenswarm.cli.dwcli`，下同。

### 多行 code 的传法（SQL/Python 任务必读）

**禁止 shell 引号内联多行 code**：Windows 下 cmd 会把跨行参数拆散，报 `dwcli: error: unrecognized arguments`。多行/含反引号/含引号的 `code` 写文件后用 `--code-file` 传：

```bash
# 1) 写 code 到文件（heredoc 首命令 cat 走 bash；<<'SQL' 带引号分隔符不展开内容）
cat > task_code.sql <<'SQL'
INSERT INTO `dws_x` (...) SELECT ... FROM `dwd_x` WHERE dt >= '2026-01-01'
SQL

# 2) --code-file 从文件读原文
python -m jiuwenswarm.cli.dwcli task create --name "..." --executor-type sql \
    --code-file task_code.sql --schedule-type none
```

单行 `code` 仍可直接 `--code "..."`。

## 写操作预览确认协议（强制）
对 create/update/delete：先解析意图整理参数 → 展示操作预览 → 等用户"确认" → 执行。只读（list/get）无需确认。

## 字段说明
- `executor-type`：sql 或 python
- `code`：SQL 文本 或 Python 代码
- `sla-minutes`：SLA 阈值分钟数（空≈timeout/60；与 timeout 区别：timeout 强制杀进程，SLA 是治理阈值）
- `timeout-s`：执行超时秒数（默认 7200=2 小时；超过 timeout 进程被 kill，状态变 timeout）
- `layer`：任务分层（ODS/DWD/DWS/ADS），供治理维度用
- `output-table`：产出表名（治理用）
- `upstream-tasks`：逗号分隔的 task_id 前驱（独立任务简单依赖链；工作流内 DAG 用 dw-workflow-define 的节点 upstream）
- `schedule-type/schedule-cron/schedule-once-at`：调度参数（设了调度参数只是"定义"，还必须 publish 上线才被调度器扫描；见下方"周期调度参数"）

## 周期调度参数（创建周期任务只需这一节，不必读源码）

### 三种调度类型（`--schedule-type`）
| 值 | 含义 | 必带参数 | 触发时机 |
|---|---|---|---|
| `none` | 不调度（默认） | 无 | 只能手动 debug 触发 |
| `cron` | 周期定时 | `--schedule-cron` | 到每个 cron 周期点自动触发 |
| `once` | 一次性定时 | `--schedule-once-at` | 到指定时刻触发一次，触发后自动置 none |

### cron 表达式格式
- **5 位标准 cron**：`分 时 日 月 周`，如 `0 2 * * *` = 每天 02:00。**推荐用 5 位**，系统自动补成 7 位 Quartz。
- 也可用 7 位 Quartz：`秒 分 时 日 月 周 年`，如 `0 0 2 * * * *`。
- **时区固定 Asia/Shanghai**，无需也不能在 cron 里指定时区。
- 校验失败（字段数非 5/7、表达式非法）时调度器**静默跳过该目标**（不报错、不触发），所以 cron 写错的表现就是"定时任务从不执行"。
- 常用示例：`0 2 * * *` 每天 02:00｜`0 */2 * * *` 每 2 小时｜`0 9 * * 1-5` 工作日 09:00｜`30 0 1 * *` 每月 1 日 00:30

### once-at 时间格式
- **ISO8601**：`2026-08-29T02:00:00`（不带时区按 Asia/Shanghai）或 `2026-08-29T02:00:00+08:00` 或 `2026-08-29T02:00:00Z`。
- 时刻已过则 publish 后立即触发一次；未到则等到该时刻。

### 端到端：建一个每天 02:00 跑的周期任务并上线（两步，不必读别的文件）
```bash
# 1. 建任务并设 cron（此时 status=draft，调度器还不扫它）
dwcli task create \
    --name "每日GMV汇总" --executor-type sql \
    --code "SELECT ..." \
    --schedule-type cron --schedule-cron "0 2 * * *"

# 2. 上线（publish 后才被调度器扫描，到点自动触发）
dwcli task publish --id <上一步返回的 task_id>
```
注意：只设 `--schedule-type/--schedule-cron` 不 publish，调度器不会扫描。一次性任务同理：`--schedule-type once --schedule-once-at "2026-08-29T02:00:00"` 后也要 publish。

## 输出
path-mode JSON：`{task_id, name, executor_type, status}` 等。创建/更新返回 id，上层编排用 id 衔接下一步（如调 dw-task-schedule publish 上线）。
