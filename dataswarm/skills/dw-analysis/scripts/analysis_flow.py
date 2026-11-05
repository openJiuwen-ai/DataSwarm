#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""analysis_flow.py —— dw-analysis skill 唯一入口（薄壳，Phase 2c）。

四层链路契约的实现分层在 flow/ 包：dwcli_client（服务端调用）→
manifest（账本）→ budget（判定）→ execute_core（执行管线）→ batch（并行）
→ commands（子命令）→ observability（打点/hints）→ cli_registry（注册表）。
本文件只做引导：flow 包就近解析 + stdout/stderr UTF-8 统一 + 调 main。

调用形态不变：`python scripts/analysis_flow.py <cmd>`；stdout 契约不变
（golden 测试锁定）；预算/账本/退出码语义见各模块 docstring。
"""
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

from flow.cli_registry import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
