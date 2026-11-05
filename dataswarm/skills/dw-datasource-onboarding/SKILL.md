---
name: dw-datasource-onboarding
type: execution
tags: [data-warehouse, datasource]
user-invocable: true
description: |
  数据源接入与工作空间绑定（只注册连接，不建表不搬数据，数据留在原地）。当用户要求"把数据接成数据源"、"接入 CSV/Excel 做分析"、"连接 MySQL/Doris/PostgreSQL 数据库"、"把数据源绑定到当前工作空间"时使用。自动识别数据类型、连通性测试、注册数据源并绑定当前工作空间，后续对话直接基于该工作空间分析数据。
  注意与数仓建设区分：用户说"导入/入仓/建仓/分层/建 ODS"（目的地是数仓、要建表搬数据）时走 dw-warehouse-design，不走本 skill。
allowed_tools: [bash, read_file, write_file]
---

# dw-datasource-onboarding

把用户的本地数据（目录/文件）或数据库接入为数据源，并绑定到当前工作空间。

## 命令执行规则（必读）

运行环境可能是 Windows/Linux/macOS，**必须平台无关**：

- **每条 bash 调用只执行一个命令**，禁止 `&&`、`;`、`|`、`>` 等 shell 链接符
- **只用单条 `python` 命令**调用本 skill 的脚本；结构化参数一律走新建的普通 `.json` 文件（`--spec-file`，先 write_file 写临时文件；禁止目录、符号链接或复用其他文件）；只有这个专用临时文件才同时传 `--delete-spec`
- **禁止在对话中回显用户提供的密码/凭据**；spec 文件可能含凭据，脚本读取后自动删除，不要手动 cat

## When To Use

- "帮我把 D:/data 目录下的数据接入进来"
- "接入这个 CSV 文件 / 这批 Excel"
- "把这台 MySQL/Doris/PostgreSQL 数据库接进来做分析"
- 接入后自动绑定当前工作空间，后续对话基于该工作空间

## When NOT TO Use

- 数据入仓 / 建 ODS 表 / 分层建仓 / 建模清洗（目的地是数仓，要建表搬数据）→ `dw-warehouse-design`
- 查询/分析已接入的数据 → `nl2sql` / `dw-analysis`
- 数据质量巡检 → `dw-inspection-orchestration`

## Step 0 — 与数仓建设（dw-warehouse-design）的路由判定

用户说「把 xxx 路径的数据接入进来」时，两个 skill 都可能被触发。**按目的地判定，不按动词**：

| 用户说法特征 | 路由 |
|---|---|
| 出现入仓目的地词：「导入」「入仓」「建仓」「数仓分层」「建 ODS/DWD/DWS/ADS」「贴源层」 | **dw-warehouse-design**（数据物理搬进 Doris 建表） |
| 只说「接入/连接/当数据源/直接分析」，无入仓词；或提到「数据源」「工作空间」；或接入对象是 **Excel 或数据库连接** | **本 skill**（仅注册数据源，数据留在原地；solution-design 只认 CSV 目录） |
| 模糊：「把 D:/data 的数据接入进来」（无入仓词也无数据源词） | **停下问用户**：「只是把数据注册成数据源直接分析，还是要建 ODS 表入仓分层？」按回答路由，禁止猜 |

## 接入流程

### Step 1 — 类型识别（本地路径）

用户提供目录/文件路径时：

```bash
python scripts/onboard.py --action detect --path <路径>
```

输出 `{ok, kind, type, files, suggested_source_id}`：

- `type=csv`（单文件或纯 CSV 目录）/ `type=excel`（单文件或纯 Excel 目录，支持 .xls/.xlsx/.xlsm，目录内每文件一个视图）→ 直接进 Step 2
- `type=mixed`（目录里 CSV 与 Excel 混杂）→ **停下来问用户**要接入哪种/哪个文件，禁止擅自拆成多个数据源
- `type=unknown` / 空目录 / 路径不存在 → 向用户澄清格式；路径不存在时提醒：路径必须是 **Agent 运行机器**的本地路径，用户本机的数据需先上传/共享到服务器

数据库接入（用户给出 host/port/database 等）不走 detect：连接参数直接从对话收集。各类型必填字段可用下面命令查（返回 types[].fields）：

```bash
python -m jiuwenswarm.cli.dwcli datasource types
```

### 连接参数询问规范（数据库接入必读）

缺连接参数时，用**一次 `ask_user` 调用**收集，**每个参数独立一题**，让用户逐项填写：

| 题序 | 参数 | 形式 |
|---|---|---|
| 1 | type（数据源类型） | 单选题：label 用中文类型名，value 用 `dwcli datasource types` 返回的类型 id（如 mysql/doris/postgresql） |
| 2 起 | host / port / user / password / database | 纯输入题（不传 options），question 写明这一项要填什么 |

- 缺哪个问哪个：用户已给出的参数（含对话上下文里已明确的）不要再问
- 收齐全部必填字段后再进 Step 2 写 spec；凭据不回显到对话（见硬性约束 B3）

### Step 2 — 写 spec 并执行接入（test → create 一体）

先 write_file 新建专用 spec JSON（必须是普通 `.json` 文件，脚本读后自动删除）：

```json
{
  "type": "csv",
  "source_id": "csv_orders",
  "params": {"path": "D:/data/orders"}
}
```

```bash
python scripts/onboard.py --action register --spec-file <spec.json> --delete-spec
```

- `source_id`：用 Step 1 返回的 `suggested_source_id`，或用户指定的合法 ID（字母/数字/`_`/`-`，长度 1-64）
- `params` 按类型：csv/excel 传 `{"path": "..."}`；mysql/doris/postgresql 传 `{"host", "port", "user", "password", "database"}`
- `description`（可选，顶层字段）：数据源用途备注，用户提到"备注/描述"时写入 spec 顶层 `"description"`，不要塞进 params
- `rebind` 默认 false；**只有用户明确同意换绑后**才置 true（见 Step 3）

脚本内部依次执行连通性测试（不落盘）→ 注册 + 自动绑定当前工作空间，输出 JSON。工作空间取自会话 env（前端选中后自动注入），脚本与 dwcli 自动读取，**禁止手动传 --workspace-id 覆盖用户选择**。

### Step 3 — 错误处理（必须停下问用户的场景）

| error_code / 场景 | 含义 | 动作 |
|---|---|---|
| `NO_WORKSPACE_CONTEXT` | 会话未关联工作空间 | **停止接入**，提示用户："请先在前端创建或选择数据工作空间，再执行数据接入"。不要代替用户创建 |
| `WORKSPACE_ALREADY_BOUND` | 工作空间已绑定其他数据源 | **停下问用户**是否换绑；用户同意后重跑 register，spec 加 `"rebind": true`；不换绑则仅添加（不传 workspace_id 创建），不影响现有绑定 |
| `SOURCE_ALREADY_BOUND` | 新源与已绑定源指向同一物理库 | **停下问用户**，禁止自动换绑 |
| `stage=test` 失败 | 路径不可达 / 数据库连不上 | 把错误转述给用户，确认路径或网络/凭据 |
| create 成功但 `bound=false` | 工作空间非界面创建（YAML 工作空间），不支持绑定 | 提示用户改用界面创建的工作空间 |

### Step 4 — 成功汇报

register 返回 `ok=true` 且 `bound=true` 后：

1. 跑 `python -m jiuwenswarm.cli.dwcli datasource current` 确认 `business_db_ref` 已指向新源
2. 向用户汇报：数据源 ID、类型、绑定的工作空间、db_ref（脚本输出已脱敏，直接用）
3. 告知后续对话可直接基于该工作空间做数据分析（nl2sql / 建仓等）

## 绑定已有数据源（不新建）

数据源已存在、只需换绑到当前工作空间时，不走接入流程，直接：

```bash
python -m jiuwenswarm.cli.dwcli datasource bind --json-file bind.json
```

bind.json 只需 `{"source_id": "<id>"}`（workspace_id 由 dwcli 从会话 env 自动注入），返回 `ok=true` 即绑定成功。

数据源支持一个工作空间注册多个；绑定是单数（换绑 = 直接 bind 新源，后写生效），换绑前同样经用户确认（B2）。

## 硬性约束

| # | 约束 |
|---|------|
| B1 | 数据源注册/绑定一律经 `onboard.py` 或 `dwcli datasource`（内部走 web API），禁止直连数据库写元数据 |
| B2 | `WORKSPACE_ALREADY_BOUND` / `SOURCE_ALREADY_BOUND` 必须停下问用户，禁止静默换绑 |
| B3 | 凭据不回显到对话、不写进 artifact；spec 文件由脚本读后自动删除 |
| B4 | 无工作空间上下文时不接入（`NO_WORKSPACE_CONTEXT`），提示用户先在前端创建/选择 |
| B5 | 混合类型目录必须澄清，禁止擅自拆分成多个数据源 |
