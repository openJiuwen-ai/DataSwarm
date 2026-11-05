# DataSwarm WHL Installation Guide

Applies to the `dataswarm` whl package (`pip install` → `dataswarm-init` →
`dataswarm-start`, three steps to a running instance). This guide covers the
whl package (`pip install` form). For the base JiuwenSwarm installation, see
[InstallGuide](InstallGuide.md).

## 1. Prerequisites

- **Python ≥ 3.11 and < 3.14** (the whl's `Requires-Python` constraint)
- **Network access** to a PyPI mirror and `gitcode.com` (the `openjiuwen`
  dependency comes from `git+https://gitcode.com/openJiuwen/agent-core.git` and
  is fetched online during installation)
- Disk: ~1.5 GB after installation (includes large dependencies such as
  chromadb / onnxruntime)
- Optional MySQL business data source: bring your own instance (see §4)

## 2. Install

Use a dedicated venv (keeps the system environment clean):

```bash
python -m venv venv
# Windows:
venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <dataswarm-xxx.whl>
# Linux/macOS:
venv/bin/python -m pip install <dataswarm-xxx.whl>
```

**Mirror note**: installation uses PyPI (or the mirror configured for pip) by
default. Some mirrors may drop the connection while downloading the `jieba`
sdist build dependencies (`IncompleteRead`; `jieba` ships no prebuilt wheel and
must be built from source). If you hit this error, pre-install jieba separately,
then install the main package:

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple jieba==0.42.1
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <dataswarm-xxx.whl>
```

A successful install makes the `dataswarm-init` / `dataswarm-start` /
`dataswarm-stop` / `dwcli` / `jiuwenbox` commands available (entry points are
listed in the whl's `entry_points`).

## 3. Initialize the runtime directory

```bash
dataswarm-init             # interactive: asks for language preference (zh/en)
echo zh | dataswarm-init   # non-interactive: defaults to zh when stdin is not a tty
```

- Runtime root defaults to `~/.dataswarm`; coexists with the base runtime
  `~/.jiuwenswarm` on the same machine without contention
- Incremental initialization: only missing files are created, existing
  configuration is never overwritten (safe to re-run)
- `-f` rebuilds from scratch (deletes the whole runtime directory) — rarely needed
- Initialization products: `config/config.yaml` (configuration), `config/.env`
  (secrets/env), `agent/workspace/skills/` (product skills), `metadata.db`, etc.

## 4. Configure the LLM and business data source

A model must be configured before the first conversation. Edit `models.defaults`
in `~/.dataswarm/config/config.yaml` (the template ships with `${API_BASE}`
placeholders and will not work as-is):

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://<your-gateway>/v1
        api_key: <your key>
        model_name: <model name>
        client_provider: OpenAI        # or Anthropic
        timeout: 300
        stream_first_chunk_timeout: 300
        stream_idle_timeout: 120
      model_config_obj:
        temperature: 0.2
        max_tokens: 32768
      is_default: true
```

- Embedding channel (used by term/semantic retrieval): set `EMBED_API_BASE` /
  `EMBED_API_KEY` / `EMBED_MODEL` in `config/.env` (any general-purpose text
  embedding model works, e.g. `BAAI/bge-m3`)
- Optional MySQL business data source: add a top-level `eval:` section to
  `config.yaml` (`mysql_host` / `mysql_port` / `mysql_user` / `mysql_password`)

## 5. Start and stop

```bash
dataswarm-start                 # start all services (foreground; Ctrl+C to stop)
dataswarm-stop                  # stop the default instance
dataswarm-start --stop <name>   # stop a named instance
```

Port group after startup (the base 19000 group shifted by +11000, so both can
run on one machine):

| Service | Address |
|---|---|
| Web UI (frontend) | http://localhost:16173 |
| WebChannel WebSocket | ws://localhost:30000/ws |
| Gateway HTTP | http://localhost:30001 |
| Web API (used by `dwcli` etc.) | `DWAGENT_BASE` defaults to http://localhost:16173 (the frontend port; static pages and `/api` are served together); 30002 exists only in source dev mode as the dedicated API listener |
| AgentServer WebSocket | ws://localhost:29092 |

Startup troubleshooting: if a port is still held by a previous instance you
will see `Another Gateway is running; refusing duplicate instance` — stop the
old instance with `dataswarm-stop`, then start again.

## 6. Installation verification checklist

1. `dataswarm-init` prints `initialized: <runtime dir>` and `config/config.yaml`
   exists
2. `dataswarm-start` logs contain no `llm_call_error` (if present, check the
   key/model in §4)
3. Port probes: 16173 / 30000 / 30001 accept connections (30002 has no listener
   in single-instance mode — normal; clients fall back to 16173)
4. Opening http://localhost:16173 in a browser shows the Web UI
