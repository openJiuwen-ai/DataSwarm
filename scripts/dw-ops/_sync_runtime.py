#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""_sync_runtime.py —— dev skills → runtime 目录同步（家规：改 skill 后必须同步）。"""
import os
import shutil
from pathlib import Path

DEV = Path("jiuwenswarm/resources/agent/workspace/skills")
# runtime 根走统一推导（JIUWENSWARM_DATA_DIR 优先，whl 独立运行时即 ~/.dataswarm；
# 2026-11-02 修复：旧硬编码 ~/.jiuwenswarm 在目录迁移后同步到已废弃旧目录）
_root = os.environ.get("JIUWENSWARM_DATA_DIR") or Path.home() / ".dataswarm"
RT = Path(_root) / "agent" / "workspace" / "skills"
DIRS = ["dw-task-define", "dw-workflow-define", "dw-task-schedule", "dw-task-execute",
        "nl2sql", "dw-report", "dw-bi-dashboard",
        "dw-db-connector", "_dw_common",
        "data-insight", "dw-warehouse-build", "dw-warehouse-design",
        "dw-analysis", "dw-task-orchestration"]

n = 0
for d in DIRS:
    src, dst = DEV / d, RT / d
    if not src.is_dir():
        print(f"skip (missing dev): {d}")
        continue
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    n += 1
    print(f"synced: {d}")

for p in RT.rglob("__pycache__"):
    shutil.rmtree(p, ignore_errors=True)
print(f"done: {n} skills synced, __pycache__ cleared")
