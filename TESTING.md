# 测试指南

测试在还原后的项目根目录（`assemble_build.py` 组装的 `build/`）运行。

## 快速开始

```bash
./scripts/dev/run_tests.sh              # 全量（等价 pytest tests/unit_tests/）
./scripts/dev/run_tests.sh -c           # 带覆盖率
./scripts/dev/run_tests.sh -u           # 仅单元测试
./scripts/dev/run_tests.sh -h           # 全部选项
```

## Python 测试（pytest）

```bash
.venv/Scripts/python.exe -m pytest tests/unit_tests/ -v --no-cov          # 全量
.venv/Scripts/python.exe -m pytest tests/unit_tests/orchestration/ -v     # 按目录
.venv/Scripts/python.exe -m pytest tests/unit_tests/web/test_ensure_dialect.py -v  # 按文件
```

约定：

- 位置 `tests/unit_tests/`，命名 `test_*.py`，按模块分目录（`orchestration/`、`web/`、
  `agents/`、`dataagent/`、`dw_*` 技能域等）
- 异步测试 `--asyncio-mode=auto`（`pytest.ini` 已配置）
- **本机注意**：pytest-cov 全包收尾在 Windows 上极慢，日常加 `--no-cov`，
  收口时再跑覆盖率
- `pytest.ini` 配置了 `filterwarnings = error`——第三方库首跑可能报
  `SyntaxWarning`（如 pysbd / openjiuwen probes 的转义序列），先用
  `python -c "import <模块>"` 预热生成 `.pyc` 再跑测试

## 前端测试（node --test）

```bash
cd jiuwenswarm/channels/web/frontend
npm run test:chat-navigation            # 按 package.json 的 test:* 脚本选择
node --test tests/xxx.test.mjs          # 单文件
npm run build                           # 改前端后的类型检查与构建门禁
```

约定：纯逻辑模块（parser / state / batcher）用 `node --test` 单测，
脚本名 `test:<module>`，位置 `frontend/tests/*.test.mjs`。

## 关键专项测试

| 测试 | 守护内容 |
| --- | --- |
| `web/test_ensure_dialect.py` | schema 双方言完整性：doris 已退役断言、mysql 覆盖 sqlite 基线 |
| `orchestration/test_schema_ddl_consistency.py` | sqlite / mysql DDL 列集一致性 |
| `agents/test_bash_tool_safety.py` | shell 工具安全 hook 与工作空间 env 注入 |
| `channel/` | web 渠道协议、轨迹接口、会话与工作空间隔离 |

## schema 变更的测试要求

改 `deploy/schema/ddl/` 时 SQLite 与 MySQL 两份方言 DDL 必须同时改，
相关一致性测试在提交前必须通过（见上表前两项）。
