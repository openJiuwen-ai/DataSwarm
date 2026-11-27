#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Thin JSON client for the task service; no local orchestration or mutable ledger."""
from __future__ import annotations

import sys
from pathlib import Path

# flow 包就近解析（与 _dw_common 同一原则）：脚本目录 = 包父目录，dev
# （resources/.../scripts/）与 runtime（~/.dataswarm/.../scripts/）布局一致。
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        # 平台命令边界统一 UTF-8（bash_tool_safety 向子进程注入 PYTHONUTF8/
        # PYTHONIOENCODING，shell 工具按 UTF-8 解码）——脚本侧显式对齐，免受
        # ambient 编码影响。errors="replace" 兜底不可编码字符，绝不因一个字符崩流程。
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

from flow.runtime_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
