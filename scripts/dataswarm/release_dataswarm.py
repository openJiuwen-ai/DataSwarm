#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""release_dataswarm.py —— DataSwarm 一键发布：跨平台 whl（对齐基座仓 Release 形态）。

发布主产物 = dataswarm-<version>-py3-none-any.whl（后端源码 + 前端 dist + resources
经 package-data 进包；pip install 后 dataswarm-init -> dataswarm-start），
附 sha256 与 release-manifest；不再产出 patch（delta 镜像本身即分发形态）。

开发/CI 共用同一条命令；内置产物自检，失败即退出，产物不外发。
构建机布局：develop_oss 全树（jiuwenswarm/）直接构建；MR 分支 / 交付仓的归档检出
（dataswarm/patch）先在归档根用 assemble_build.py 回放出 build/、在 build/ 内本地提交后
再运行本命令——CI 全流程见 CI 工程脚本 ci_build.sh。

用法:
    python scripts/dataswarm/release_dataswarm.py [--skip-frontend-build]

发布机要求：git / Python / Node.js（npm，构建前端；--skip-frontend-build 可跳过）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Windows 管道下 stdout 默认 cp936 会把中文输出变乱码——统一 UTF-8 边界
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def _run(script: str, *extra: str) -> None:
    print(f"\n>>> python scripts/dataswarm/{script} {' '.join(extra)}")
    proc = subprocess.run([sys.executable, str(SCRIPTS / script), *extra])
    if proc.returncode != 0:
        print(f"[FAIL] {script} 退出码 {proc.returncode}，发布中止", file=sys.stderr)
        sys.exit(proc.returncode)


def main() -> int:
    # 原样转发全部参数（--version / --skip-frontend-build 都由 build_dataswarm_dist.py
    # 自己解析）——此前只转发 --skip-frontend-build，CI 指定的 --version 被静默吞掉，
    # 产物版本恒为 pyproject 缺省值。
    _run("build_dataswarm_dist.py", *sys.argv[1:])

    print("\n[RELEASE] 门禁全过，产物就绪：")
    release_dir = SCRIPTS.parent.parent / "dist" / "dataswarm-release"  # 仓根/dist（SCRIPTS=scripts/dataswarm/）
    print("── 发布产物（跨平台 whl）──")
    for f in sorted(release_dir.glob("*")):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
    print("\n  安装: pip install <whl> -> dataswarm-init -> dataswarm-start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
