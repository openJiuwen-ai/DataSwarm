---
name: dw-task-schedule
description: 调度生命周期原子能力。当用户提到"上线任务/下线任务/发布任务/暂停工作流/恢复工作流/审核通过/任务审核/工作流审核/草稿转上线/调度状态转换"时使用。只管任务/工作流的调度生命周期状态转换（草稿↔上线、暂停恢复、审核），不建不改定义、不执行、不查实例。通过 `dwcli task|workflow|instance` 调用。
allowed_tools: [bash, read_file]
---

# dw-task-schedule · 调度生命周期原子能力

只管**调度状态转换**：任务/工作流从草稿→上线（开始被调度）、下线、暂停恢复、审核通过。

> 本 skill 全部数据访问经`dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，不再直连数据库。**要求 web 服务常驻**；服务不可达时 dwcli
> 退出码 2 并提示连接失败。

## 何时触发
- 用户要**上线/下线/暂停/恢复/审核**任务或工作流
- 用户说"把这个任务上线"、"暂停这个工作流"、"审核通过这个节点"、"恢复工作流"

## 何时不触发
- 建任务/工作流定义 → `dw-task-define` / `dw-workflow-define`
- 调试/执行/看实例 → `dw-task-execute`

## 状态转换

```
task:     draft ⇄ published（publish/unpublish）
          任务实例不暂停（需求 5b：任务实例不暂停）

workflow: draft ⇄ published（publish/unpublish）
          running ⇄ paused（pause/resume）
          waiting_review → running（approve，审核通过续跑）
```

## CLI

```bash
# 上线/下线（写操作，先预览确认）
dwcli task publish   --id <task_id>
dwcli task unpublish --id <task_id>
dwcli workflow publish   --id <workflow_id>
dwcli workflow unpublish --id <workflow_id>

# 暂停/恢复（写操作）
dwcli workflow pause --id <workflow_id>           # 暂停最近 running 实例
dwcli instance resume --id <instance_id>          # 恢复 paused 工作流（同步续跑）

# 审核（写操作，approve 会同步续跑工作流）
dwcli instance approve --id <instance_id>          # 审核通过 waiting_review 节点
```

> `dwcli` = `python -m jiuwenswarm.cli.dwcli`，下同。

## 关键说明
- **publish 后才被调度器扫描**：草稿态的任务/工作流不会被定时触发。调度参数（schedule-cron/once-at）在 dw-task-define / dw-workflow-define 建定义时就设好，本 skill 只做 draft→published 状态转换使其生效。
- **publish 时调度参数已就绪**：周期任务的 `--schedule-type/--schedule-cron/--schedule-once-at` 在建任务/工作流时指定（见对应 skill），publish 命令本身不带调度参数，只传 `--id`。
- **approve 同步续跑**：approve 会标 waiting_review 节点为 success，然后同步续跑工作流，返回最终状态。
- **pause 只对工作流**：任务实例不暂停（需求）；工作流可暂停/恢复。
- **instance_id 从哪来**：`dwcli workflow debug`（见 dw-task-execute）后返回 instance_id，卡 waiting_review 时用本 skill approve。

## 上线周期任务/工作流（端到端视角）
周期调度的参数在建定义阶段设好，本 skill 只负责 publish 让其生效：

| 对象 | 已设调度参数 | 上线命令 |
|---|---|---|
| 周期任务 | `dwcli task create --schedule-type cron --schedule-cron "0 2 * * *"` | `dwcli task publish --id <task_id>` |
| 一次性任务 | `dwcli task create --schedule-type once --schedule-once-at "2026-08-29T02:00:00"` | 同上 |
| 周期工作流 | `dwcli workflow create --schedule-type cron --schedule-cron "0 2 * * *"` | `dwcli workflow publish --id <wf_id>` |

publish 后调度器（每 10 秒扫一次）会在下个 cron 周期点 / once-at 时刻自动触发。已 publish 的对象再次 publish 是幂等的（无副作用）。

## 写操作预览确认协议
对 publish/unpublish/pause/approve/resume：先展示当前状态和目标状态 → 等用户"确认" → 执行。

## 输出
path-mode JSON：`{status, ref, id}` 或 `{status, instance_id, final_status}`。approve/resume 返回续跑后的最终实例状态。
