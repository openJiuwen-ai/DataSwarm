<p align="center">
  <h1 align="center">DataSwarm</h1>
</p>

<p align="center">
  <strong>连接你的数据，开启智能分析</strong>
</p>

<p align="center">
  <a href="./TESTING.md">测试指南</a>
  ·
  <a href="https://gitcode.com/openJiuwen/DataSwarm/releases">Releases</a>
  ·
  <a href="https://gitcode.com/openJiuwen/jiuwenswarm">基座 JiuwenSwarm</a>
  ·
  <a href="./README.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" />
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/base-workswarm0.2.6-orange.svg" alt="Base" />
  <img src="https://img.shields.io/badge/os-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg" alt="OS Support" />
</p>

**DataSwarm 是基于 [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm) 多智能体基座构建的企业数据智能体应用，它连接现有数据平台与业务知识，以业务理解与可靠执行作为核心设计原则，以结果可信为目标，支持用户通过自然语言对话完成问数与深度分析，得到一份可信的分析结果。**

> **注意**：当前的开发形态与整体架构在未来可能产生较大变化，本文档描述的流程与结构以最新架构为准。

> **交付形态说明**：本开源仓库以 **Web 服务（尝鲜版）** 形态提供，用于功能试用；
> 亦可按 whl / 源码方式安装运行。

## 核心能力

首版本聚焦两条主线：**数据接入**与**数据分析**。

它打通「数据接入 → 数据分析」的完整链路：数据团队用自然语言接入数据源、
探查元数据，Agent 基于真实元数据与指标口径执行分析，直达可审计的分析产出，
全程留痕、可回溯。

### 数据接入

向数据团队提供从零到可用的数据源接入闭环：

| 能力 | 说明 |
| --- | --- |
| **多数据源接入** | MySQL / SQLite 等数据源向导式接入与声明式配置，csv / Excel 文件源直读 |
| **工作空间隔离** | 多工作空间按需绑定数据源，会话、库表与产物全程隔离 |
| **元数据自动采集** | 接入即采集库表清单、Schema 与行数概览，Web 端数据资产页即查 |
| **知识派生** | 接入后自动抽取 schema 候选并深化为业务知识，供后续分析与查询使用 |
| **凭据安全** | 连接串去凭据管理，凭据只在运行时解析，不入库不外泄 |

### 数据分析

自然语言直达可信分析结论，全程以 manifest 账本审计：

| 能力 | 说明 |
| --- | --- |
| **自然语言分析** | 四层分析链路（会话 → 上下文 → 规划 → 编译执行），`dw-analysis` 端到端产出结论与产物 |
| **指标确定性查询** | `metric-v2`：指标定义 / 修订 / 发布 / 口径绑定全生命周期，`query` 按权威口径编译 SQL 执行，query_log 自带审计 |
| **术语与语义召回** | 业务术语解析（glossary）消歧口径；语义向量召回（semantic）定位相关表与知识 |
| **元数据探查与取数** | 库表清单 / Schema / 行数概览 / 数据预览，`dwcli sql` 只读查询 |
| **分析追踪可视化** | 分析追踪页以 manifest 为唯一审计来源，逐步还原分析过程 |

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│  DataSwarm 工作台（Web）  CLI（dwcli）               │
├─────────────────────────────────────────────────────┤
│  DataSwarm 能力域（本仓库）                          │
│  数据接入（datasource/元数据/知识派生）                │
│  数据分析（dw-analysis 链路/指标 metric-v2/术语/语义）  │
├─────────────────────────────────────────────────────┤
│  JiuwenSwarm 基座（多智能体协作 / 渠道 / 工具 / 记忆）│
└─────────────────────────────────────────────────────┘
```

技术栈：Python ≥ 3.11 · FastAPI · React + TypeScript + Vite · sqlglot · SQLite / MySQL 双方言元数据库。

## 仓库模型（重要）

本仓库以**归档形态**发布。DataSwarm 基于基座
[JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm)（当前 `workswarm0.2.6`）增量开发：

- `dataswarm/patch/dataswarm.patch` = **相对基座版本的全部内容差异**（代码、测试、文档、
  配置）——还原完整项目的唯一自足通道
- 还原方法：`clone 基座 tag + git apply patch` —— 一步还原出本次发布的完整可运行项目
  （`assemble_build.py` 一键完成）
- `dataswarm/skills/`、`scripts/`、`docs/` 等工程资产以文件形态直接放置，便于在仓库页
  浏览；完整内容以 patch 为准

```bash
python scripts/dataswarm/assemble_build.py     # 还原：clone 基座 tag + apply patch
```

贡献与验证流程详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 快速开始

> 使用已发布 whl 包安装？见 [docs/zh/DataSwarmWHL安装指南.md](./docs/zh/DataSwarmWHL安装指南.md)。

### 前置要求

- Python ≥ 3.11（< 3.14）与 [uv](https://docs.astral.sh/uv/)
- Node.js（前端构建）
- 任一 OpenAI / Anthropic 协议兼容的模型网关

### 1. 组装与安装

```bash
python scripts/dataswarm/assemble_build.py     # 还原：clone 基座 tag + apply patch → build/
cd build
uv sync                                        # 后端依赖（见下方提示）
cd jiuwenswarm/channels/web/frontend
npm install                                    # 前端依赖（独立于后端，可单独安装）
cd ../../../..                                 # 回到 build/
```

> **`uv sync` 提示**：首次安装需下载约 1.5 GB+ 依赖（含 chromadb / onnxruntime /
> faiss-cpu / grpcio / playwright 等大包）。弱网环境易在 10 MB 级大包上超时——
> 可重复执行 `uv sync`（已下载部分进缓存、逐次补齐），或
> `UV_HTTP_TIMEOUT=600 uv sync` 放宽超时。国内链路还可改用镜像源加速，如
> `uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple`——但部分
> 公共镜像缺 `openai-codex` 等包，会报 No solution found，届时请回落官方源
> 并配合上述重试。前后端依赖相互独立：后端失败不影响
> 前端单独安装，反之亦然。
>
> **`npm install` 提示**：安装完成后 `npm audit` 可能报出依赖树中已知的若干
> 安全通告（随锁定版本与通告库更新而变化）。其中 `vite` / `rollup` / `eslint`
> 属开发期依赖，不进入构建产物；**请勿执行 `npm audit fix --force`**——它会把
> 运行时依赖 `exceljs` 从 4.x 降到 3.x 并破坏导出功能。

### 2. 配置模型

编辑 `~/.dataswarm/config/config.yaml`（首次启动自动生成模板）：

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://your-gateway:3000/   # Anthropic 协议示例
        api_key: ${API_KEY}
        model_name: your-model
        client_provider: Anthropic
      is_default: true
```

### 3. 启动

```bash
cd build && ./scripts/dev/restart-dev.sh
# 工作台: http://127.0.0.1:16173
# Web: 30000 / API: 30002 / 网关: 30001 / AgentServer: 29092
```

### 4. 上手数据接入与分析

在工作台（http://127.0.0.1:16173）完成，无需命令行：

1. 左下角「切换空间」→「新建工作空间」
2. 左侧「数据接入」→「新增数据源」→ 选择「文件与对象存储」下的 Excel / CSV，
   填写**服务端**上的文件或目录路径，点「测试连接」通过后「创建」
3. 创建即绑定当前工作空间，并自动触发知识派生（schema 候选抽取 + LLM 深化）；
   等列表「知识派生」列显示「N 条候选 · M 已深化」即可
4. 左侧「任务」页直接用自然语言问数；需要完整分析链路时，在问题里加上
   「生成一个报告」

> 首次使用还需在「设置 → 知识图库」填写知识图连接信息（语义采集与语义层依赖它），
> 保存后写入仓库根 `.env`，重启服务生效。
> 带界面截图与示例数据的完整演示见 [examples/](./examples/)。

命令行入口（`dwcli`）主要供脚本、Agent 与自动化使用，例如：

```bash
cd build
.venv/Scripts/dwcli datasource list --workspace-id <ws>       # 数据源与绑定关系
.venv/Scripts/dwcli databases tables --db <库> --workspace-id <ws>
.venv/Scripts/dwcli glossary resolve --workspace-id <ws> --q <术语>
.venv/Scripts/dwcli metric-v2 query --workspace-id <ws> --json-file query.json
```

## CLI 一览（dwcli）

| 命令域 | 用途 |
| --- | --- |
| `datasource` / `databases` | 数据接入：数据源与库表元数据 |
| `metric-v2` / `glossary` / `semantic` | 数据分析：指标确定性查询、术语解析、语义召回 |
| `analysis` | 数据分析：会话 / 上下文 / 规划 / 编译 / 执行 / 结果核查 |
| `sql` | 数据分析：只读 SQL 取数 |

## 测试

```bash
cd build
.venv/Scripts/python.exe -m pytest tests/unit_tests/ -v --no-cov
./scripts/dev/run_tests.sh -c        # 带覆盖率
```

详见 [TESTING.md](./TESTING.md)。

## 文档

- [examples/](./examples/) —— Excel 数据源接入 → 语义抽取 → 问数的界面入门示例（含截图）
- [示例分析报告](examples/report/sales-analysis-report.md) —— 由入门示例数据生成的报告样例（[HTML 版](examples/report/sales-analysis-report.html)）
- [CONTRIBUTING.md](./CONTRIBUTING.md) —— 贡献指南（仓库模型 / 验证 / 贡献流程）
- [deploy/schema/README.md](./deploy/schema/README.md) —— 系统表 schema 真相源约定
- [Releases](https://gitcode.com/openJiuwen/DataSwarm/releases) —— 版本发布记录

## 贡献

还原项目（clone 基座 tag + apply patch）→ 在还原出的项目里改码验证 → 以补丁形式提交贡献
（issue 说明动机与验证，详见 [CONTRIBUTING.md](./CONTRIBUTING.md)）。提交信息使用中文
Conventional Commits。

## 致谢

DataSwarm 构建于 [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm)
（基座版本 `workswarm0.2.6`）之上——感谢基座提供的多智能体协作、渠道与技能演进能力。

## License

[Apache-2.0](./LICENSE)

## 免责声明

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。
