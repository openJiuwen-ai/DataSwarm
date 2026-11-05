#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""sync_schema.py —— 开发期 schema 同步：deploy/schema → 运行目录薄复制。

生产分发不依赖本脚本（ensure 的仓库目录自动发现可覆盖开发态）；打包 wheel 的
自动物化列为后续事项。改 deploy/schema 后运行一次即可让运行时副本目录生效。

用法:
    python scripts/dw-ops/sync_schema.py                # 默认 src=repo/deploy/schema dst=<数据根>/deploy-schema
    python scripts/dw-ops/sync_schema.py --dst <dir>    # 自定义目标
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "deploy" / "schema"
# 运行时副本目录跟随数据根（JIUWENSWARM_DATA_DIR 优先，无 env 回退 ~/.jiuwenswarm）：
# 必须与 ensure.py 的 _runtime_deploy_schema_dir 同一推导——本脚本刷新的副本正是
# ensure 解析顺序里的运行时候选，两边目录不一致时改 schema 永远不生效。
_data_root = os.environ.get("JIUWENSWARM_DATA_DIR")
DEFAULT_DST = (Path(_data_root) if _data_root else Path.home() / ".jiuwenswarm") / "deploy-schema"


def main() -> int:
    ap = argparse.ArgumentParser(description="deploy/schema 同步到运行目录")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST)
    args = ap.parse_args()
    if not (args.src / "ddl").is_dir():
        print(f"[ERROR] 源目录缺少 ddl/: {args.src}", file=sys.stderr)
        return 1
    shutil.copytree(args.src, args.dst, dirs_exist_ok=True)
    # 镜像语义：仓库已删的 DDL 不得残留在运行目录——ensure 按 sqlite 基线守卫
    # 比对的是运行目录文件集，孤儿文件会让已退役的表继续被建（2026-10-10
    # dw_bi_metrics 退役时实测踩到：sync 只增不删，ensure 继续建废表报错）。
    removed = 0
    for existing in args.dst.rglob("*"):
        if existing.is_file():
            rel = existing.relative_to(args.dst)
            if not (args.src / rel).exists():
                existing.unlink()
                removed += 1
    for empty_dir in sorted((d for d in args.dst.rglob("*") if d.is_dir()),
                            key=lambda d: len(d.parts), reverse=True):
        if not any(empty_dir.iterdir()):
            empty_dir.rmdir()
    n = len(list((args.dst / "ddl").glob("*.sql")))
    print(f"[ok] {args.src} -> {args.dst}（ddl 文件 {n} 个，清理孤儿 {removed} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
