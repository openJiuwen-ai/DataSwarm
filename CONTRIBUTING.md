# 贡献指南

感谢关注 DataSwarm!本文说明如何获取可运行的项目、验证改动并提交贡献。

## 项目形态

DataSwarm 基于基座 [JiuwenSwarm](https://gitcode.com/openJiuwen/jiuwenswarm)
(当前 `workswarm0.2.6`)增量开发。本仓库以**归档形态**发布：

- `dataswarm/patch/dataswarm.patch` —— **相对基座版本的全部内容差异**(代码、测试、
  文档、配置),是还原完整项目的**唯一自足通道**
- `dataswarm/skills/`、`scripts/`、`deploy/schema/`、`docs/`、示例与文档等工程资产
  以文件形态直接放置,便于浏览;完整内容以 patch 为准

## 还原可运行项目

```bash
# 方式一:一键组装(推荐)
python scripts/dataswarm/assemble_build.py
cd build
uv sync
cd jiuwenswarm/channels/web/frontend
npm install
cd ../../../..

# 方式二:手动(等价)
git clone --no-checkout https://gitcode.com/openJiuwen/jiuwenswarm.git && cd jiuwenswarm
git -c core.autocrlf=false checkout workswarm0.2.6
git apply /path/to/DataSwarm/dataswarm/patch/dataswarm.patch
```

还原结果即本次发布的完整项目源码,直接可运行。

> **Windows 注意**:patch 的行尾是 LF,应用前工作区必须同为 LF。上面的
> `--no-checkout` + `-c core.autocrlf=false checkout` 已保证这一点;若已按默认
> 配置检出(工作区为 CRLF,`core.autocrlf=true` 时常见),直接 apply 会报
> `patch does not apply`——重新按上面命令检出,或加 `--ignore-whitespace` 兜底。

## 贡献流程

1. **改码**:在还原出的项目里修改(IDE 直接打开),运行验证:
   ```bash
   ./scripts/dev/restart-dev.sh                 # 前端 http://localhost:16173
   ```
2. **验证**(提交前必须通过):
   ```bash
   ruff check <改动的 .py>                      # 增量检查,勿跑全仓
   uv run pytest tests/unit_tests/ -v           # 相关测试
   cd jiuwenswarm/channels/web/frontend && npm run build   # 前端有改动时
   ```
   改 `deploy/schema/ddl/` 时,SQLite 与 MySQL 两份方言 DDL 必须同时改。
3. **提交贡献**:在 issue 中说明改动动机与验证方式,并以补丁形式附上改动
   (`git diff` 或还原出的项目里 `git format-patch`);维护者评审后会合入本仓库。
   提交说明使用中文 Conventional Commits(`type(scope): 描述`,
   如 `fix(orchestration): 修复定时任务从不触发`)。

## 测试

- 后端:`uv run pytest tests/unit_tests/ -v`(详见 [TESTING.md](./TESTING.md))
- 前端:见 `jiuwenswarm/channels/web/frontend` 的 `npm run test:*` 脚本
- 还原一致性:维护者侧负责校验基座 + patch 完整还原

## 硬性约束

- 禁止硬编码密码 / 密钥 / token;连接串去凭据后再入 artifact / 历史表
- 元数据库新代码保持方言无关(SQLite / MySQL 双方言)
- `run_pipeline.py` 全量流水线必须同步阻塞执行,禁止 background
- 禁止提交 `__pycache__/`、`.pyc`、真实凭据、`.coverage`、`htmlcov/`

## 行为准则

请保持友善与专业:议题讨论对事不对人,提交说明写清动机与验证方式,
不确定的设计决策先开 issue 讨论再动手。
