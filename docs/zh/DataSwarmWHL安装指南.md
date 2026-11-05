# DataSwarm WHL 安装指南

> **目标**：通过 `pip install` 安装 `dataswarm` whl 包并完成初始化、配置与启动，
> 三步可用（`pip install` → `dataswarm-init` → `dataswarm-start`）。适用于
> 服务器部署、CLI 场景或希望用自有 Python 环境的用户。
>
> 本文介绍 **whl 包**（pip 安装形态）。基座 JiuwenSwarm 通用安装方式见
> [安装指南](安装指南.md)。
>
> **英文版：** [English Version](../en/DataSwarmInstallGuideWHL.md)

---

## 1. 前置条件

- **Python ≥ 3.11 且 < 3.14**（whl 的 `Requires-Python` 约束）
- **网络可达** PyPI 镜像与 `gitcode.com`（依赖 `openjiuwen` 来自
  `git+https://gitcode.com/openJiuwen/agent-core.git`，安装时需在线拉取构建）
- 磁盘：安装后约 1.5 GB（含 chromadb / onnxruntime 等大依赖）
- 如需 MySQL 业务数据源：自备实例（见 §4 可选配置）

## 2. 安装

建议使用独立 venv（不污染系统环境）：

```bash
python -m venv venv
# Windows:
venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <dataswarm-xxx.whl>
# Linux/macOS:
venv/bin/python -m pip install <dataswarm-xxx.whl>
```

**镜像注意**：默认走 PyPI（或本机 pip 配置的镜像）。部分镜像源在下载 `jieba` 的
sdist 构建依赖时可能连接中断（`IncompleteRead`；`jieba` 无预编译 wheel，需源码构建）。
遇到同类报错，先单独预装 jieba 再安装主包：

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple jieba==0.42.1
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <dataswarm-xxx.whl>
```

安装成功的标志：`dataswarm-init` / `dataswarm-start` / `dataswarm-stop` /
`dwcli` / `jiuwenbox` 等命令可用（入口见 whl 的 `entry_points`）。

## 3. 初始化运行时目录

```bash
dataswarm-init             # 交互模式：询问语言偏好 (zh/en)
echo zh | dataswarm-init   # 非交互：stdin 非 tty 时自动采用默认语言 zh
```

- 运行时根目录默认 `~/.dataswarm`；与基座 `~/.jiuwenswarm` 同机共存、互不抢占
- 增量初始化：只补缺失文件，不覆盖已有配置（可安全重复执行）
- `-f` 为破坏性重建（删整个运行时目录），一般不需要
- 初始化产物：`config/config.yaml`（配置）、`config/.env`（密钥/env）、
  `agent/workspace/skills/`（产品技能）、`metadata.db` 等

## 4. 配置 LLM 与业务数据源

安装后**必须**先配置模型才能对话。编辑 `~/.dataswarm/config/config.yaml` 的
`models.defaults`（模板默认是 `${API_BASE}` 等占位符，不配置无法使用）：

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://<你的网关>/v1
        api_key: <你的 key>
        model_name: <模型名>
        client_provider: OpenAI        # 或 Anthropic
        timeout: 300
        stream_first_chunk_timeout: 300
        stream_idle_timeout: 120
      model_config_obj:
        temperature: 0.2
        max_tokens: 32768
      is_default: true
```

- 向量通道（术语/语义检索用）：`config/.env` 里配 `EMBED_API_BASE` /
  `EMBED_API_KEY` / `EMBED_MODEL`（通用文本向量模型均可，如 `BAAI/bge-m3`）
- MySQL 业务数据源（可选）：`config.yaml` 顶层加 `eval:` 段
  （`mysql_host` / `mysql_port` / `mysql_user` / `mysql_password`）

## 5. 启动与停止

```bash
dataswarm-start                 # 启动全部服务（前台运行，Ctrl+C 停止）
dataswarm-stop                  # 停止常规实例（default）
dataswarm-start --stop <name>   # 停止命名实例
```

启动后端口组（与基座 19000 组整组 +11000，同机共存互不抢占）：

| 服务 | 地址 |
|---|---|
| Web UI（前端） | http://localhost:16173 |
| WebChannel WebSocket | ws://localhost:30000/ws |
| Gateway HTTP | http://localhost:30001 |
| Web API（`dwcli` 等工具用） | `DWAGENT_BASE` 默认 http://localhost:16173（前端端口，静态页面与 `/api` 一体服务）；30002 仅源码开发形态的独立 API 监听 |
| AgentServer WebSocket | ws://localhost:29092 |

启动异常排查：端口被上一轮实例占用时会报
`Another Gateway is running; refusing duplicate instance`——先
`dataswarm-stop` 停干净再启动。

## 6. 安装验证清单

1. `dataswarm-init` 输出 `initialized: <运行时目录>` 且 `config/config.yaml` 存在
2. `dataswarm-start` 日志无 `llm_call_error`（有则检查 §4 的 key/模型名）
3. 端口探测：16173 / 30000 / 30001 可连（30002 单实例形态无监听，属正常——
   客户端回落 16173）
4. 浏览器打开 http://localhost:16173 可见 Web UI
