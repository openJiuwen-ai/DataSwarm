# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DataSwarm skill 侧运行时根目录解析（唯一入口，skill 脚本勿再散落硬编码）。

优先级：
1. ``JIUWENSWARM_DATA_DIR`` 环境变量——``dataswarm_boot.py``（dataswarm* 入口）
   默认注入 ``~/.dataswarm``；skill 脚本在 agent 服务子进程里跑，天然继承；
   独立调试时 ``export JIUWENSWARM_DATA_DIR=<工作区>`` 即可指向目标。
2. ``~/.dataswarm``——release 形态的回退默认（未注入 env 的裸跑场景；
   dev 源码形态 restart-dev.sh 注入同值，两种形态一致）。

替换目标：skill 脚本里 ``Path.home() / ".jiuwenswarm"`` 的所有硬编码。
"""

from __future__ import annotations

import os
from pathlib import Path

_FALLBACK_DIR_NAME = ".dataswarm"


def workspace_root() -> Path:
    """DataSwarm 运行时根目录（env 优先，回退 ``~/.dataswarm``）。"""
    env_root = os.getenv("JIUWENSWARM_DATA_DIR")
    if env_root:
        return Path(env_root).expanduser()
    return Path.home() / _FALLBACK_DIR_NAME


def config_root() -> Path:
    """配置目录：``<workspace_root>/config``。"""
    return workspace_root() / "config"


def artifact_root() -> Path:
    """巡检/分析产物目录：``<workspace_root>/artifact``。"""
    return workspace_root() / "artifact"


def agent_root() -> Path:
    """agent 工作区目录：``<workspace_root>/agent``。"""
    return workspace_root() / "agent"


def llm_config_path() -> Path:
    """LLM 配置 yaml：``<workspace_root>/config/config.yaml``。"""
    return config_root() / "config.yaml"
