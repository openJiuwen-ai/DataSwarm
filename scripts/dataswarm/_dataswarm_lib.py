#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""_dataswarm_lib.py —— scripts/dataswarm 公共库（git 子进程 / 仓库根定位）。

历史注记：旧的 overlay manifest（dataswarm_overlay.yaml）/ 路径映射 / 增量账本
随「全树镜像」模型退役（2026-10-31 切共源 + 三目录归档模型）；归档回放与组合
验证见 assemble_build.py 与 fork/verify_equivalence.py。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_GIT_BIN = shutil.which("git") or "git"  # 外部命令取绝对路径（禁用 PATH 注入）


class OverlayError(RuntimeError):
    """构建/扫描工具失败。"""


def git(*args: str, cwd: Path | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    """git 子命令（默认主仓），stdout 按原始 bytes（patch/树对象不得经文本解码）。"""
    proc = subprocess.run([_GIT_BIN, *args], cwd=cwd or REPO_ROOT, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise OverlayError(
            f"git {' '.join(args)}\n{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc
