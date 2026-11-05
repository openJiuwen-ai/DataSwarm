<p align="center">
  <h1 align="center">DataSwarm</h1>
</p>

<p align="center">
  <strong>Connect Your Data, Unlock Intelligent Analysis</strong>
</p>

<p align="center">
  <a href="./TESTING.md">Testing Guide</a>
  ·
  <a href="https://gitcode.com/openJiuwen/DataSwarm/releases">Releases</a>
  ·
  <a href="https://gitcode.com/openJiuwen/jiuwenswarm">Base: JiuwenSwarm</a>
  ·
  <a href="./README.zh.md">简体中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" />
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/base-workswarm0.2.6-orange.svg" alt="Base" />
  <img src="https://img.shields.io/badge/os-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg" alt="OS Support" />
</p>

**DataSwarm is an enterprise data-agent application built on the [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm) multi-agent foundation. It connects existing data platforms with business knowledge, takes business understanding and reliable execution as its core design principles, and targets trustworthy results — users can complete data Q&A and in-depth analysis through natural-language conversation and get back a trustworthy analytical result.**

> **Note**: The current development workflow and architecture may change substantially in the future; where they differ, the latest architecture takes precedence.

> **Open-source statement**: this repository is currently provided for feature trial and evaluation only.

## Core Capabilities

The first release focuses on two main tracks: **Data Onboarding** and **Data Analysis**.

It connects the full chain from data onboarding to data analysis: data teams onboard
data sources and explore metadata in natural language, while the Agent executes
analysis grounded in real metadata and metric definitions, delivering auditable
analytical outputs, with every step traced and replayable.

### Data Onboarding

A closed loop that takes data teams from zero to usable data sources:

| Capability | Description |
| --- | --- |
| **Multi-source onboarding** | Wizard-driven onboarding and declarative configuration for MySQL / SQLite and other sources; csv / Excel file sources are read directly |
| **Workspace isolation** | Multiple workspaces bind data sources on demand; sessions, databases and tables, and artifacts stay isolated throughout |
| **Automatic metadata collection** | Onboarding collects the database/table catalog, schema, and row-count overview right away, instantly browsable on the web data-assets page |
| **Knowledge derivation** | After onboarding, schema candidates are automatically extracted and deepened into business knowledge for subsequent analysis and queries |
| **Credential safety** | Connection strings are managed with credentials stripped out; credentials are resolved only at runtime — never stored, never leaked |

### Data Analysis

From natural language straight to trustworthy analytical conclusions, audited end to end against a manifest ledger:

| Capability | Description |
| --- | --- |
| **Natural-language analysis** | Four-layer analysis chain (session → context → planning → compile & execute); `dw-analysis` produces conclusions and artifacts end to end |
| **Deterministic metric queries** | `metric-v2`: full lifecycle of metric definition / revision / publication / definition binding; `query` compiles and executes SQL against the authoritative definition, with query_log auditing built in |
| **Glossary & semantic recall** | Business-term resolution (glossary) disambiguates metric definitions; semantic vector recall (semantic) locates relevant tables and knowledge |
| **Metadata exploration & data access** | Database/table catalog / schema / row-count overview / data preview; read-only queries via `dwcli sql` |
| **Analysis-trace visualization** | The analysis-trace page treats the manifest as its single audit source and reconstructs the analysis step by step |

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  DataSwarm Workbench (Web)       CLI (dwcli)        │
├─────────────────────────────────────────────────────┤
│  DataSwarm Capability Domain (this repository)      │
│  Data Onboarding (datasource/metadata/knowledge)    │
│  Data Analysis (dw-analysis chain/metric-v2/        │
│  glossary/semantic)                                 │
├─────────────────────────────────────────────────────┤
│  JiuwenSwarm Foundation (multi-agent collaboration/ │
│  channels/tools/memory)                             │
└─────────────────────────────────────────────────────┘
```

Tech stack: Python ≥ 3.11 · FastAPI · React + TypeScript + Vite · sqlglot · SQLite / MySQL dual-dialect metadata database.

## Repository Model (Important)

This repository is published in **archive form**. DataSwarm is developed as a delta on
top of the base [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm)
(currently `workswarm0.2.6`):

- `dataswarm/patch/dataswarm.patch` = **all content differences against the base
  version** (code, tests, docs, config) — the single self-contained channel for
  restoring the project
- Restore with `clone base tag + git apply patch` — one step restores the complete
  runnable project as published (`assemble_build.py` automates it)
- `dataswarm/skills/`, `scripts/`, `docs/` and other engineering assets are placed as
  regular files for direct browsing; the patch remains the complete source of truth

```bash
python scripts/dataswarm/assemble_build.py     # restore: clone base tag + apply patch
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the contribution and verification flow.

## Quick Start

> Installing from a released whl package instead? See [docs/en/DataSwarmInstallGuideWHL.md](./docs/en/DataSwarmInstallGuideWHL.md).

### Prerequisites

- Python ≥ 3.11 (< 3.14) and [uv](https://docs.astral.sh/uv/)
- Node.js (for the frontend build)
- Any OpenAI / Anthropic protocol-compatible model gateway

### 1. Assemble & Install

```bash
python scripts/dataswarm/assemble_build.py     # restore: clone base tag + apply patch → build/
cd build
uv sync                                        # backend dependencies (see note below)
cd jiuwenswarm/channels/web/frontend
npm install                                    # frontend dependencies (independent of backend)
cd ../../../..                                 # back to build/
```

> **Note on `uv sync`**: the first install downloads 1.5 GB+ of dependencies
> (chromadb / onnxruntime / faiss-cpu / grpcio / playwright, among other large
> wheels). On slow links, 10 MB+ wheels may time out — re-running `uv sync` is
> safe (completed downloads are cached and resumed), or use
> `UV_HTTP_TIMEOUT=600 uv sync` to raise the timeout. Readers in mainland China
> may speed things up with a mirror index, e.g.
> `uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple` — note,
> however, that some public mirrors lack packages such as `openai-codex` and
> fail with "No solution found"; fall back to the official index and retry in
> that case. Backend and frontend
> dependencies are fully independent: either one can be installed separately.
>
> **Note on `npm install`**: `npm audit` may report known advisories in the
> locked dependency tree. `vite` / `rollup` / `eslint` are dev-only and never
> ship in the build output. **Do not run `npm audit fix --force`** — it would
> downgrade the runtime dependency `exceljs` from 4.x to 3.x and break export.

### 2. Configure the Model

Edit `~/.dataswarm/config/config.yaml` (a template is generated automatically on first start):

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://your-gateway:3000/   # Anthropic protocol example
        api_key: ${API_KEY}
        model_name: your-model
        client_provider: Anthropic
      is_default: true
```

### 3. Start

```bash
cd build && ./scripts/dev/restart-dev.sh
# Workbench: http://127.0.0.1:16173
# Web: 30000 / API: 30002 / Gateway: 30001 / AgentServer: 29092
```

### 4. Try Out Data Onboarding and Analysis

Do it in the web workbench (http://127.0.0.1:16173) — no CLI required:

1. Bottom-left "Switch workspace" → "Create workspace"
2. Left nav "Data Access" → "Add data source" → pick Excel / CSV under
   "Files & Object Storage"; enter the **server-side** file or directory path,
   run "Test connection", then "Create"
3. Creating the source binds it to the current workspace and automatically triggers
   knowledge derivation (schema candidates + LLM enrichment). Wait until the
   "Knowledge derivation" column shows "N candidates · M enriched"
4. Ask questions in natural language on the "Tasks" page; add "generate a report"
   to the question when you want the full analysis chain and a deliverable report

> On first use, fill in the knowledge-graph connection under "Settings → Knowledge Graph"
> (semantic collection and the semantic layer depend on it); saving writes the repo-root
> `.env`, and the service must be restarted for it to take effect.
> For a complete walkthrough with UI screenshots and sample data, see [examples/](./examples/).

The `dwcli` entry points are mainly for scripts, agents, and automation, for example:

```bash
cd build
.venv/Scripts/dwcli datasource list --workspace-id <ws>       # sources and bindings
.venv/Scripts/dwcli databases tables --db <db> --workspace-id <ws>
.venv/Scripts/dwcli glossary resolve --workspace-id <ws> --q <term>
.venv/Scripts/dwcli metric-v2 query --workspace-id <ws> --json-file query.json
```

## CLI Overview (dwcli)

| Command domain | Purpose |
| --- | --- |
| `datasource` / `databases` | Data onboarding: data sources and table metadata |
| `metric-v2` / `glossary` / `semantic` | Data analysis: deterministic metric queries, term resolution, semantic recall |
| `analysis` | Data analysis: session / context / planning / compilation / execution / result verification |
| `sql` | Data analysis: read-only SQL data access |

## Testing

```bash
cd build
.venv/Scripts/python.exe -m pytest tests/unit_tests/ -v --no-cov
./scripts/dev/run_tests.sh -c        # with coverage
```

See [TESTING.md](./TESTING.md) for details.

## Documentation

- [examples/](./examples/) — UI walkthrough from Excel onboarding through semantic extraction to analysis (with screenshots)
- [Sample analysis report](examples/report/sales-analysis-report.md) — report generated from the walkthrough data ([HTML version](examples/report/sales-analysis-report.html))
- [CONTRIBUTING.md](./CONTRIBUTING.md) — contributing guide (repository model / verification / contribution flow)
- [deploy/schema/README.md](./deploy/schema/README.md) — source-of-truth conventions for system-table schemas
- [Releases](https://gitcode.com/openJiuwen/DataSwarm/releases) — release notes

## Contributing

Restore the project (clone the base tag + apply patch) → code and verify on the
restored project → submit contributions as patches (see
[CONTRIBUTING.md](./CONTRIBUTING.md) for the flow). Commit messages follow
Conventional Commits, written in Chinese.

## Acknowledgements

DataSwarm is built on [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm)
(base version `workswarm0.2.6`) — thanks to the base for its multi-agent collaboration,
channel, and skill-evolution capabilities.

## License

[Apache-2.0](./LICENSE)

## Disclaimer

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.
