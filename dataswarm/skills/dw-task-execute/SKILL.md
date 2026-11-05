---
name: dw-task-execute
description: 执行与实例原子能力。当用户提到"调试任务/调试工作流/跑一下任务/执行任务/看任务实例/看执行结果/看实例状态/看执行日志/stdout/stderr/任务产出"时使用。产生运行实例并同步执行，查实例/任务运行/日志。不建不改定义、不碰调度状态。通过 `dwcli task|workflow|instance` 调用。
allowed_tools: [bash, read_file]
---

# dw-task-execute · 执行与实例原子能力

**产生运行实例** + **查实例/任务运行/日志**。debug 产生调试实例并同步执行（不走周期调度），返回实例状态和结果。

> **API 化说明（方案 docs/skill-cli-web-api-plan.md 批次 2）**：本 skill 全部数据访问经
> `dwcli`（`python -m jiuwenswarm.cli.dwcli`，端口自动发现（`DWAGENT_BASE`
> 可覆盖）走 web API，不再直连数据库。**要求 web 服务常驻**；服务不可达时 dwcli
> 退出码 2 并提示连接失败。

## 何时触发
- 用户要**调试/执行**任务或工作流
- 用户要**查执行实例/任务运行状态/日志/产出**
- 用户说"调试一下这个任务"、"跑一下看看结果"、"看任务执行日志"、"实例状态怎么样"

## 何时不触发
- 建任务/工作流定义 → `dw-task-define` / `dw-workflow-define`
- 上线/暂停/审核 → `dw-task-schedule`

## CLI

```bash
# 执行（两步：debug 返回 instance_id → instance wait 轮询至终态）
dwcli task debug     --id <task_id>          # 202 已受理，返回 instance_id
dwcli workflow debug --id <workflow_id>      # 同上
dwcli instance wait --id <instance_id>       # 轮询至 success/failed/cancelled（对 Agent 同步阻塞）

# 查询（只读，无需确认）
dwcli instance list [--ref-type task|workflow] [--trigger-type debug|cron|once|manual]
dwcli instance get       --id <instance_id>   # 含 task_runs 状态
dwcli instance task-runs --id <instance_id>   # 各节点退出码/时长/产出
dwcli instance logs      --id <run_id> [--stream stdout|stderr]
```

> `dwcli` = `python -m jiuwenswarm.cli.dwcli`，下同。

## 关键说明
- **debug + wait 两步**：`debug` 返回 202（异步执行已受理）和 instance_id，再调 `instance wait` 轮询至终态（success/failed/cancelled），对 Agent 仍表现为同步阻塞。这是 dwcli 的设计（§6.1），不把两步打包成单命令。
- **工作流 debug 可能卡 waiting_review**：`instance wait` 默认轮询至终态（success/failed/cancelled），`waiting_review` 是**非终态**——不传 `--timeout` 时会一直轮询到超时（默认 1800s）才带着当前态返回。若想让「卡审核→去 approve」的流程不被 30 分钟空等卡住，先给 `instance wait` 设一个短 `--timeout`（如 `--timeout 30`），wait 超时返回当前 `waiting_review` 态后，调 `dwcli instance approve --id <instance_id>`（见 dw-task-schedule）审核续跑，approve 后再 wait 至终态。
- **大日志走文件**：stdout/stderr 落盘 `~/.dataswarm/artifact/orchestration/runs/<instance_id>/`，logs 命令读返回前 8000 字符。
- **debug 不进周期调度**：调试实例 trigger_type=debug，上线后才会被调度器周期触发。

## 典型调用链（上层编排组合）
```
dwcli task debug --id <id> → 拿 instance_id
dwcli instance wait --id <instance_id> → 拿终态 status
dwcli instance task-runs --id <instance_id> → 拿 run_id, output_rows
dwcli instance logs --id <run_id> --stream stdout → 看执行输出

# 工作流卡审核时（waiting_review 非终态，不设短 timeout 会空等 30 分钟）
dwcli workflow debug --id <id> → instance_id
dwcli instance wait --id <instance_id> --timeout 30   # 短 timeout，超时返回当前 waiting_review 态
dwcli instance approve --id <instance_id> → 续跑
dwcli instance wait --id <instance_id> → 拿最终终态
```

## 输出
path-mode JSON：`{status, instance_id, ref, task_id|workflow_id}` 或 `{run_count, task_runs}` 或 `{run_id, stream, log}`。大日志不灌进 context，按需读文件。
