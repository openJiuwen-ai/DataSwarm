#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""assemble_build.py —— 还原组装：build/ = 基线 tag 浅 clone + git apply patch。

V4 单通道：patch 恒含全部内容差异（代码/测试/文档/配置，零排除），组装即
「clone 基线 tag + apply patch」——与开发仓该稳定点逐字节一致，直接可运行。

运行位置：归档形态 checkout（MR 分支 / 交付仓 develop、main 分支；本地模拟：MR worktree）。
本脚本自包含（零内部依赖），随 sync 清单同步到 MR 分支。
CI 用途（CI 工程脚本 ci_build.sh）：回放后在 build/ 内本地提交即可构建 whl（归档根目录
直接跑 release_dataswarm.py 会被其布局 guard 拒绝）。

基线 = 项目常量（jiuwenswarm 公共仓 + tag），不读 commit message（patch 只记录内容
差异、不记录基准 tag，tag 必须由外部供给）：环境变量 DATASWARM_BASELINE_REPO /
DATASWARM_BASELINE_TAG 可覆盖（CI 与基线升级时用）。克隆默认跳过 LFS 媒体拉取
（基线 docs 视频 ~92MB，构建不需要，工作区留指针）。

用法:
    python scripts/dataswarm/assemble_build.py            # 首次组装 / 幂等重建
    python scripts/dataswarm/assemble_build.py --refresh  # 删除旧 build 重建（换基线/污染时）
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build"
PATCH_FILE = "dataswarm/patch/dataswarm.patch"

# 基线（项目常量）：基线升级时改这里（debug/fork 工具同口径），CI 可用同名环境变量覆盖
BASELINE_REPO = "https://gitcode.com/openJiuwen/jiuwenswarm.git"
BASELINE_TAG = "workswarm0.2.6"

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


class AssembleError(RuntimeError):
    """组装失败。"""


def git(*args: str, cwd: Path | None = None, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd or REPO_ROOT, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise AssembleError(f"git {' '.join(args)}\n{proc.stderr.decode('utf-8', errors='replace').strip()}")
    return proc


def _resolve_baseline() -> dict:
    """基线 = 脚本常量（公共仓 + tag），环境变量可覆盖；不读 commit message。

    Returns:
        {"repo": 基线仓 URL, "ref": 基线 tag}
    """
    return {
        "repo": os.environ.get("DATASWARM_BASELINE_REPO", "").strip() or BASELINE_REPO,
        "ref": os.environ.get("DATASWARM_BASELINE_TAG", "").strip() or BASELINE_TAG,
    }


def _clone_once(repo: str, ref: str) -> None:
    # 基线 docs 媒体是 LFS 实体（~92MB，构建用不到）：跳过 smudge 只留指针，git add 会
    # 原样透传指针、树不变（GIT_LFS_SKIP_SMUDGE=0 可显式拉取）。CI 上还避免 LFS 鉴权失败。
    env = {**os.environ}
    env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    # --no-checkout + autocrlf=false 物化：工作区必须与 blob 逐字节一致（LF 原样），
    # 否则 Windows 全局 core.autocrlf=true 会把基线写成 CRLF，patch 上下文（LF）全部失配
    git("clone", "--depth", "1", "--branch", ref, "--no-checkout", repo, str(BUILD_DIR), env=env)
    git("-c", "core.autocrlf=false", "checkout", "-f", cwd=BUILD_DIR, env=env)


def _ssh_to_https(url: str) -> str | None:
    """SSH 形态地址转 HTTPS（CI 容器通常没有基线仓的 SSH key；公共仓 HTTPS 只读可克隆）。"""
    for pattern in (r"^git@([^:]+):(.+)$", r"^ssh://(?:[^@/]+@)?([^/]+)/(.+)$"):
        m = re.match(pattern, url.strip())
        if m:
            return f"https://{m.group(1)}/{m.group(2)}"
    return None


def _clone_baseline(repo: str, ref: str) -> None:
    print(f"[*] 浅克隆基线 {repo} @ {ref} -> {BUILD_DIR.name}/ ...")
    try:
        _clone_once(repo, ref)
    except AssembleError:
        https = _ssh_to_https(repo)
        if not https:
            raise
        print(f"[!] 克隆失败，改用 HTTPS 重试: {https}", file=sys.stderr)
        import shutil
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        _clone_once(https, ref)


def _apply_patch() -> None:
    """patch 源自 HEAD 归档树对象层（工作区字节不可信），apply 前显式预检。"""
    data = git("cat-file", "blob", f"HEAD:{PATCH_FILE}", check=False)
    if data.returncode != 0 or not data.stdout:
        if data.returncode != 0:
            raise AssembleError(f"HEAD 树缺 {PATCH_FILE}")
        print("[=] patch 为空——跳过 apply")
        return
    import tempfile
    with tempfile.NamedTemporaryFile(prefix="dataswarm-replay-", suffix=".patch", delete=False) as fh:
        fh.write(data.stdout)
        patch_tmp = fh.name
    try:
        proc = git("-c", "core.autocrlf=false", "apply", "--check", patch_tmp,
                   cwd=BUILD_DIR, check=False)
        if proc.returncode != 0:
            raise AssembleError("patch 预检失败（基线与 manifest 记录的 tag 不符？）:\n"
                                + proc.stderr.decode("utf-8", errors="replace").strip())
        git("-c", "core.autocrlf=false", "apply", patch_tmp, cwd=BUILD_DIR)
    finally:
        # CI 基础镜像 python3=3.7.4：unlink 的 missing_ok= 是 3.8+ 参数——patch 已 apply
        # 成功仍会崩在临时文件清理（2026-11-03 ST 流水线实测），改 3.7 可用的等价写法
        try:
            Path(patch_tmp).unlink()
        except FileNotFoundError:
            pass
    print("[OK] patch 已应用")


def main() -> int:
    ap = argparse.ArgumentParser(description="组装 build/（基线浅 clone + apply patch）")
    ap.add_argument("--refresh", action="store_true", help="删除旧 build 重建（换基线/怀疑污染时）")
    args = ap.parse_args()

    meta = _resolve_baseline()
    print(f"[*] 基线: {meta['repo']} @ {meta['ref']}")

    if args.refresh and BUILD_DIR.exists():
        import shutil
        shutil.rmtree(BUILD_DIR)
        print("[refresh] 已删除旧 build/")

    if not BUILD_DIR.exists():
        _clone_baseline(meta["repo"], meta["ref"])
    else:
        head = git("rev-parse", "HEAD", cwd=BUILD_DIR, check=False).stdout.decode().strip()
        tag = git("rev-parse", f"refs/tags/{meta['ref']}^{{commit}}",
                  cwd=BUILD_DIR, check=False).stdout.decode().strip()
        if head != tag:
            print("[WARN] build HEAD 与基线 tag 不一致——建议 --refresh 重建", file=sys.stderr)

    # 基线已就位：无条件还原到干净基线再重放（apply 需要干净基线）
    # -x：patch 新增的 skill 等文件被 .gitignore 覆盖、属「被忽略的未跟踪文件」，
    # 不带 -x 的 clean 不会删，二次 apply 会报 already exists
    git("-c", "core.autocrlf=false", "reset", "--hard", "-q", "HEAD", cwd=BUILD_DIR)
    git("clean", "-fdx", cwd=BUILD_DIR, check=False)
    _apply_patch()

    print(f"[OK] 组装完成: {BUILD_DIR} = 基线 {meta['ref']} + patch，"
          "与开发稳定点逐字节一致（LFS 媒体保指针）")
    print("     用途：CI 冒烟（build/ 内跑测试）与 whl 构建（CI 工程脚本 ci_build.sh）；"
          "本地开发请用 develop_oss 全树")
    return 0


if __name__ == "__main__":
    sys.exit(main())
