---
name: dw-task-orchestration
description: 数据开发任务编排端到端。当用户提到"建任务并调试/建工作流并跑一下/一站式任务管理/任务编排全套/数据开发一站式"等需要多个编排动作串起来时使用。本 skill 不自己实现逻辑，只按用户意图依次调用下层原子 skill（dw-task-define / dw-workflow-define / dw-task-schedule / dw-task-execute）完成组合操作。单一动作直接路由到对应原子 skill，不经过本 skill。
allowed_tools: [bash, read_file]
---

# dw-task-orchestration · 任务编排端到端编排层

不自己做检测/执行，而是按用户一站式意图**串联下层原子 skill**。

> **API 化说明（方案 docs/skill-cli-web-api-plan.md 批次 2）**：所有动作经
> `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，不再有各原子 skill 的本地 `*_cli.py` 可执行文件（源文件已随
> API 化迁移撤销）。各命令的完整参数见对应原子 skill 的「CLI」节，本文件只讲串联。

## 为什么需要编排层

- 用户一句话往往跨多个原子能力（"建个查 GMV 的任务并调试看结果" = define 建任务 + execute 调试 + execute 看日志）
- 主 agent 临场串联易漏步骤、顺序乱、忘记拿 id 衔接
- 本 skill 固化常见组合路径，把 id 接力集中在一处

## 何时触发
- 用户要**一站式/组合操作**（建任务并调试、建工作流并跑、上线并验证）
- 用户意图跨多个原子 skill 的边界

## 何时不触发（直接路由到原子）
- 单一动作：只建任务 → `dw-task-define`；只调试 → `dw-task-execute`；只上线 → `dw-task-schedule`

## 串联的原子 skill

| skill | 职责 | 何时调 |
|---|---|---|
| `dw-task-define` | 任务定义 CRUD | 需要建/改任务定义时 |
| `dw-workflow-define` | 工作流定义 + 节点 DAG | 需要建/改工作流结构时 |
| `dw-task-schedule` | 调度生命周期（上线/暂停/审核） | 需要 publish/approve/resume 时 |
| `dw-task-execute` | 执行 + 实例 + 日志 | 需要 debug/看实例/看日志时 |

## 关键：id 接力（path-mode JSON）

各原子动作输出 path-mode JSON（关键 id），本 skill 负责把上一步的 id 衔接到下一步：
- `dwcli task create ...` → 返回 `task_id`
- `dwcli workflow create ...` → 返回 `workflow_id`
- `dwcli workflow nodes --id <workflow_id> --json-file nodes.json` → 配节点 DAG（nodes.json 顶层 `{"nodes":[...]}`，同批内可自定义 `node_id` 供 `upstream_nodes` 引用）
- `dwcli task debug --id <task_id>` / `dwcli workflow debug --id <workflow_id>` → 返回 `instance_id`
- `dwcli instance wait --id <instance_id>` → 轮询至终态，拿 `run_id`
- `dwcli instance logs --id <run_id>` → 看输出
- 工作流 debug 卡 `waiting_review` → `dwcli instance approve --id <instance_id>` 续跑（见 dw-task-schedule）

## 典型组合路径

> `dwcli` = `python -m jiuwenswarm.cli.dwcli`，下同。写操作（create/nodes/publish/approve）先预览 → 用户确认 → 执行。

### 路径 A：建任务 → 调试 → 看结果
```bash
# 1. 建任务（见 dw-task-define）→ 拿 task_id
dwcli task create --name "..." --executor-type sql --code "..." [--sla-minutes N --layer L --output-table T]
# 2. 调试（见 dw-task-execute）→ 拿 instance_id
dwcli task debug --id <task_id>
# 3. 轮询至终态 → 拿 status/run_id
dwcli instance wait --id <instance_id>
# 4. 看执行输出
dwcli instance logs --id <run_id> --stream stdout
```

### 路径 B：建工作流（串多任务 + 审核）→ 调试 → 审核 → 看结果
```bash
# 1. 建各任务（可多次，见 dw-task-define）→ 各 task_id
dwcli task create ...
# 2. 建工作流（见 dw-workflow-define）→ 拿 workflow_id
dwcli workflow create --name "..." [--schedule-type cron --schedule-cron "0 9 * * *"]
# 3. 配节点 DAG：task_id 引用上一步；需审核的节点置 wait_review=true。
#    nodes 是结构化数组，写 JSON 文件（顶层 {"nodes":[...]} 对象，非裸数组）再传路径。
dwcli workflow nodes --id <workflow_id> --json-file nodes.json
# 4. 调试（见 dw-task-execute）→ 拿 instance_id（可能卡 waiting_review）
dwcli workflow debug --id <workflow_id>
dwcli instance wait --id <instance_id>
# 5. 卡在审核节点（waiting_review 非终态）：报告用户确认，通过后 approve 续跑（见 dw-task-schedule）
dwcli instance approve --id <instance_id>
# 6. 续跑后继续 wait 至终态 / 看日志
dwcli instance wait --id <instance_id>
dwcli instance logs --id <run_id> --stream stdout
```

### 路径 C：建任务 → 设调度 → 上线
```bash
# 1. 建任务并设 cron（schedule 参数在 create 时设好，见 dw-task-define）
dwcli task create --name "..." --executor-type sql --code "..." \
    --schedule-type cron --schedule-cron "0 9 * * *" ...
# 2. 上线：publish 后才被调度器扫描（见 dw-task-schedule）
dwcli task publish --id <task_id>
# 3. 周期触发后查自动实例（trigger_type=cron）
dwcli instance list --ref-type task --trigger-type cron
```

## 操作确认协议
各原子 skill 的写操作（create/update/delete/nodes/publish/approve/resume）都自带预览确认。本编排层**不绕过**这些确认——每步写动作仍需用户确认（避免一站式批量执行误操作）。

## 设计约束
- **只串联不实现**：本 skill 不写 CLI 脚本，全部调 `dwcli task|workflow|instance` 走 web API（原子 skill 的 `*_cli.py` 源文件已随 API 化撤销，勿再引用）
- **id 接力在上下文**：本 skill 把上一步返回的 id 记在对话上下文，喂给下一步（不依赖文件）
- **错误中断**：任一步失败即停下报告，不继续后续步骤（如 define 失败不 execute）
- **不替代原子 skill 的确认**：一站式不等于批量执行，每步写动作仍预览确认
- **命令参数以原子 skill 为准**：单条命令缺省/可选参数先看对应原子 skill 的「CLI」节或 `dwcli <group> <action> --help`，不臆造参数
