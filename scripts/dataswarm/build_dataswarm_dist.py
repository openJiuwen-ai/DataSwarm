#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""build_dataswarm_dist.py —— DataSwarm 发布产物构建：whl（对齐基座仓 Release 形态）。

产物（dist/dataswarm-release/）：
    dataswarm-<norm-version>-py3-none-any.whl  唯一构建产物，跨平台（py3-none-any，
                                       Windows / Linux / macOS 通用）
    *.sha256                            发布页校验和（sha256sum -c 兼容）
    dataswarm-<version>-release-manifest.json  溯元数据（commit / 基线 / 前端统计）

whl 内容 = 后端源码 + 前端 dist（package-data）+ resources（config/skills 模板），
安装即完整产品：pip install <whl> -> dataswarm-init -> dataswarm-start（全套 CLI
见 pyproject [project.scripts]）。版本经 PEP 440 规范化（0.2.5.beta1 -> 0.2.5b1）。
Release 页面其余资产（source code zip/tar.gz 等）由 GitCode 打 tag 自动生成。

构建机布局（本地 / 云端 CI 同一条命令）：
- **在 develop_oss 全树检出上运行**（jiuwenswarm/ 布局）：git 对象层导出 HEAD 树。
- 归档形态检出（MR 分支 / 交付仓 develop，dataswarm/patch 布局）的**根目录**不支持
  直接构建：CI 先在归档根跑 assemble_build.py 回放出 build/、在 build/ 内本地提交
  （HEAD 即全树），再在 build/ 内运行本脚本——全流程见 CI 工程脚本 ci_build.sh。

构建要点：
- 源码树从 git 对象层导出（git archive），不经过文件系统——Windows 无执行位、
  CRLF 转换都不会引入假差异（与 verify patch 门禁同一哲学）
- 前端 dist 是构建产物不在 git 树里，构建机上 npm ci + npm run build 后注入
  导出树（setuptools package-data 收集；node_modules 被 exclude-package-data 排除）
- --version 同步改写导出树 pyproject 的 version，whl 文件名随之（不脱节）

用法:
    python scripts/dataswarm/build_dataswarm_dist.py
    python scripts/dataswarm/build_dataswarm_dist.py --skip-frontend-build
    python scripts/dataswarm/build_dataswarm_dist.py --version 0.3.0
    （在 develop_oss 检出的仓根上运行）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dataswarm_lib import OverlayError, git, REPO_ROOT  # noqa: E402

OUT_DIR = REPO_ROOT / "dist" / "dataswarm-release"
DIST_IN_PKG = "jiuwenswarm/channels/web/frontend/dist"  # 包内路径：组合态布局，与构建机无关

# 导出树裁剪：纯开发态内容不进构建现场（whl 内容另由 setuptools packages.find 决定）
EXCLUDES = [
    ":(exclude).claude",                                  # AI 工具配置
    ":(exclude)tests",                                    # 单元测试
    ":(exclude)scripts/dataswarm/fork",                   # 归档工具（工作流专用，不进产品包）
    ":(exclude)scripts/legacy-build",                     # 旧完整树仓库遗留，路径已失效
    ":(exclude)orchestration-inspection-bridge-plan.md",  # 内部方案文档
]

# whl 产物自检断言（package-data / entry_points 关键项）
WHL_MUST_EXIST = [
    "jiuwenswarm/channels/web/frontend/dist/index.html",  # 前端 dist
    "jiuwenswarm/start_services.py",                      # 服务编排（dataswarm-start）
    "jiuwenswarm/init_workspace.py",                      # 运行时初始化（dataswarm-init）
    # 系统表 DDL（ensure 包内兜底；与 resolve_schema_dir 的 sentinel 同文件）
    "jiuwenswarm/resources/deploy-schema/ddl/dw_datasources.ddl.sqlite.sql",
]
WHL_MUST_ENTRY_POINTS = ["dataswarm-start", "dataswarm-init"]


def _pyproject_version() -> str:
    """主仓 pyproject.toml 的 version（原样路径，主仓即权威）。"""
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _baseline_ref() -> str | None:
    """基线 tag：读 fork_config 的 baseline 段（无则 None）。"""
    path = REPO_ROOT / "scripts" / "dataswarm" / "fork" / "fork_config.yaml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("  ") and line.strip().startswith("ref:"):
            return line.strip().split(":", 1)[1].strip()
    return None


def _detect_layout() -> str:
    """构建机检出布局：develop_oss 全树（jiuwenswarm/）；归档检出（dataswarm/patch）不支持构建。

    build/ 组装现场（基线浅 clone，含 jiuwenswarm/）会被误判——由 _assert_packable 拦截。
    """
    if (REPO_ROOT / "jiuwenswarm").is_dir():
        return "develop_oss"
    if (REPO_ROOT / "dataswarm" / "patch").is_dir():
        raise OverlayError(
            "当前是归档形态检出（MR 分支 / 交付仓 CI 布局）——先在归档根跑 "
            "scripts/dataswarm/assemble_build.py 回放出 build/，并在 build/ 内本地提交"
            "（HEAD 即全树）后再构建；CI 全流程见 CI 工程脚本 ci_build.sh")
    raise OverlayError(f"未识别的检出布局: {REPO_ROOT}（既无 jiuwenswarm/ 也无 dataswarm/patch）")


def _assert_packable() -> None:
    """拦截误用：HEAD 树必须含本脚本（基线浅 clone / build 组装现场不含，archive 只会导出基线树）。"""
    proc = git("cat-file", "-e", "HEAD:scripts/dataswarm/build_dataswarm_dist.py", check=False)
    if proc.returncode != 0:
        raise OverlayError(
            "HEAD 树不含 scripts/dataswarm/build_dataswarm_dist.py——疑似在 build/ 组装现场"
            "或基线浅 clone 上运行（git archive 只会导出基线树，产物缺 delta）。"
            "请在 develop_oss 检出的仓根上运行。")


def _frontend_dir(layout: str, tree_ref: str) -> tuple[Path, Path | None]:
    """前端构建目录：develop_oss 工作区全树。

    Returns:
        (frontend_dir, export_root)：export_root 恒 None（保留二元组签名兼容调用方）。
    """
    return REPO_ROOT / "jiuwenswarm" / "channels" / "web" / "frontend", None


def _source_tree_ref(layout: str) -> str:
    """源码树导出 ref：develop_oss 全树用 HEAD。"""
    return "HEAD"


def _build_frontend(frontend_dir: Path) -> None:
    """发布机上构建前端。node_modules 缺失时 npm ci（lock 锁定，干净机确定性）；
    已存在时直接 npm run build（开发机上常有 dev server 运行，npm ci 的删除重装
    会被运行中的 esbuild/vite 文件锁挡住，增量构建互不干扰）。"""
    npm = shutil.which("npm")
    if not npm:
        raise OverlayError("未找到 npm——发布机需要 Node.js 构建前端（或改用 --skip-frontend-build）")
    if not (frontend_dir / "package-lock.json").exists():
        raise OverlayError(f"package-lock.json 缺失: {frontend_dir}")
    steps = [[npm, "run", "build"]]
    if not (frontend_dir / "node_modules").exists():
        steps.insert(0, [npm, "ci"])
    for args in steps:
        print(f">>> {' '.join(args)}  (cwd={frontend_dir})")
        subprocess.run(args, cwd=frontend_dir, check=True)


def _frontend_dist(frontend_dir: Path, skip_build: bool) -> Path:
    dist = frontend_dir / "dist"
    if not skip_build:
        _build_frontend(frontend_dir)
    if not (dist / "index.html").exists():
        raise OverlayError(f"前端产物缺失 {dist}/index.html（构建失败或未构建）")
    return dist


def _build_whl(tree_ref: str, version: str, dist_dir: Path, out_dir: Path) -> Path:
    """构建 Python 包 whl（基座仓 Release 同款资产；pip/uv 安装后 dataswarm-init -> dataswarm-start）。

    构建路径对齐基座仓 scripts/build.sh：pip 装 build/wheel 后
    `python -m build --wheel --no-isolation`（用当前 python 环境，不依赖 uv build）。
    从 tree_ref 导出干净树构建（对象层，不经过构建机工作区）；前端 dist 注入后由
    pyproject 的 package-data 打进包（node_modules 已被 exclude-package-data 排除）。
    version 同步改写导出树 pyproject 的 version，whl 文件名随之（不脱节）；
    文件名版本经 PEP 440 规范化（0.2.5.beta1 -> 0.2.5b1）。
    """
    export = Path(tempfile.mkdtemp(prefix="dataswarm-whl-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(_archive_tree_bytes(tree_ref))) as tf:
            tf.extractall(export, filter="data")
        # 前置断言：构建现场残缺（如导出树缺基座包目录）会让 setuptools 静默产空包
        for probe in ("pyproject.toml", "jiuwenswarm/start_services.py", "jiuwenbox/src"):
            if not (export / probe).exists():
                top = sorted(p.name for p in export.iterdir())[:20]
                raise OverlayError(f"构建现场残缺：导出树缺 {probe}（顶层: {top}）")
        # 诊断：pyproject 可解析性与遗留构建入口（UNKNOWN-0.0.0 空包的排查线索）
        with open(export / "pyproject.toml", "rb") as fh:
            py_meta = tomllib.load(fh)
        proj = py_meta.get("project", {})
        print(f"    导出树 pyproject: name={proj.get('name')} version={proj.get('version')} "
              f"backend={py_meta.get('build-system', {}).get('build-backend')} "
              f"setup.py={(export / 'setup.py').exists()} setup.cfg={(export / 'setup.cfg').exists()}")
        dist_in = export / DIST_IN_PKG
        shutil.copytree(dist_dir, dist_in, dirs_exist_ok=True)
        # 系统表 DDL 注入包内（同前端 dist：仓根资产构建时注入，不进 git 树）。源取
        # 导出树自身（组合树含全部 DDL，dev/镜像两布局一致）；whl 目标机由
        # ensure.resolve_schema_dir 的包内 resources/deploy-schema 兜底命中
        schema_src = export / "deploy" / "schema"
        if not (schema_src / "ddl").is_dir():
            raise OverlayError(f"导出树缺系统 schema 真相源: {schema_src}")
        shutil.copytree(schema_src, export / "jiuwenswarm" / "resources" / "deploy-schema",
                        dirs_exist_ok=True)
        py = export / "pyproject.toml"
        text = re.sub(r'^version\s*=\s*"[^"]*"', f'version = "{version}"',
                      py.read_text(encoding="utf-8"), count=1, flags=re.M)
        py.write_text(text, encoding="utf-8")
        whl_out = export / "whl-out"
        # 构建环境诊断：CI 镜像可能预设 PYTHONPATH 指向系统 site-packages，把旧
        # setuptools（<61，不认 PEP 621 [project]）插到 sys.path 最前，遮蔽 venv 内
        # 新版本 → UNKNOWN-0.0.0 空包。构建子进程清掉 PYTHONPATH，并打印实际来源。
        env_py = sys.executable
        build_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        probe = subprocess.run(
            [env_py, "-c",
             "import sys, setuptools;"
             "print('setuptools', setuptools.__version__, setuptools.__file__);"
             "print('sys.path[:3]', sys.path[:3])"],
            capture_output=True, text=True, env=build_env)
        if probe.returncode != 0:
            raise OverlayError("当前 python 环境缺 setuptools（先 pip install setuptools build wheel）"
                               f"：{probe.stderr.strip()[-500:]}")
        print("    构建环境: " + probe.stdout.strip().replace("\n", "\n    "))
        print(f">>> python -m build --wheel --no-isolation  (cwd={export})")
        proc = subprocess.run([env_py, "-m", "build", "--wheel", "--no-isolation",
                               "--outdir", str(whl_out)],
                              cwd=export, capture_output=True, text=True, env=build_env)
        if proc.stdout.strip():
            print(proc.stdout.strip()[-2000:])
        if proc.returncode != 0:
            raise OverlayError(f"python -m build 失败（exit {proc.returncode}）: "
                               f"{proc.stderr.strip()[-2000:]}")
        if proc.stderr.strip():
            print(f"    build stderr 尾部: {proc.stderr.strip()[-1500:]}")
        whls = sorted(whl_out.glob("*.whl"))
        if len(whls) != 1:
            raise OverlayError(f"预期恰 1 个 whl，实际 {len(whls)}: {[w.name for w in whls]}")
        dest = out_dir / whls[0].name
        shutil.copy2(whls[0], dest)
        _verify_whl(dest, export)  # 构建即自检：失败时带构建现场诊断
        return dest
    finally:
        shutil.rmtree(export, ignore_errors=True)


def _archive_tree_bytes(tree_ref: str) -> bytes:
    """git archive 导出树（bytes 直传，mode 来自 tree 对象不经文件系统）。

    显式 ``core.autocrlf=false``：构建机 autocrlf=true 时 git archive 会把
    文本 smudge 成 CRLF，whl 内 skills/resources 与 develop_oss 工作区（LF）
    byte 漂移——安装版 dataswarm-init 将 runtime skills 覆盖为 CRLF 版本后，
    评测 preflight 的 dev/runtime sha256 一致性检查必挂（对齐 archive_delta.py
    的 eol 纪律：归档产物生成一律禁用 autocrlf）。
    """
    return git("-c", "core.autocrlf=false",
               "archive", "--format=tar", tree_ref, "--", ".", *EXCLUDES).stdout


def _write_sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (path.parent / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def _verify_whl(whl_path: Path, export: Path | None = None) -> None:
    """产物自检：前端 dist 在包、关键 CLI 入口齐全、node_modules 零残留。

    失败时输出诊断（whl 实际条目 + 构建现场状态），用于定位空包类环境差异
    （如 setuptools 版本对 packages.find 的处理变化）。
    """
    with zipfile.ZipFile(whl_path) as zf:
        names = set(zf.namelist())
        missing = [p for p in WHL_MUST_EXIST if p not in names]
        if missing:
            diag = [f"whl 条目 {len(names)} 个，前 20: {sorted(names)[:20]}"]
            if export is not None:
                diag.append(f"构建现场顶层: {sorted(p.name for p in export.iterdir())}")
                diag.append(f"jiuwenswarm/ 存在: {(export / 'jiuwenswarm').is_dir()}，"
                            f"jiuwenbox/src 存在: {(export / 'jiuwenbox/src').is_dir()}")
            raise OverlayError("自检失败，whl 缺失关键文件: "
                               f"{missing}\n  诊断: " + "\n  诊断: ".join(diag))
        if any("node_modules" in n for n in names):
            raise OverlayError("自检失败: whl 混入 node_modules")
        ep_file = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        if not ep_file:
            raise OverlayError("自检失败: whl 缺 entry_points.txt")
        eps = zf.read(ep_file).decode()
        miss_ep = [e for e in WHL_MUST_ENTRY_POINTS if f"{e} =" not in eps]
        if miss_ep:
            raise OverlayError(f"自检失败: whl 缺 CLI 入口 {miss_ep}")


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 DataSwarm 发布包（后端源码 + 前端 dist + 运行脚本）")
    ap.add_argument("--version", default=None, help="版本号（缺省取 pyproject.toml 的 version）")
    ap.add_argument("--skip-frontend-build", action="store_true",
                    help="跳过 npm ci/build（要求前端 dist 已是最新）")
    args = ap.parse_args()

    version = args.version or _pyproject_version()
    layout = _detect_layout()
    _assert_packable()
    head = git("rev-parse", "HEAD").stdout.decode().strip()
    head_short = head[:9]
    baseline_ref = _baseline_ref()

    print(f"[1/5] 源码树就绪：{layout} 检出，HEAD {head_short}（基线 {baseline_ref or '未标记'}）")
    tree_ref = _source_tree_ref(layout)
    frontend_dir, _export_root = _frontend_dir(layout, tree_ref)
    dist_dir = _frontend_dist(frontend_dir, args.skip_frontend_build)
    return _package(tree_ref, version, dist_dir, layout, head, baseline_ref)


def _package(tree_ref: str, version: str, dist_dir: Path, layout: str,
             head: str, baseline_ref: str | None) -> int:
    stats = {"frontend_files": sum(1 for p in dist_dir.rglob("*") if p.is_file()),
             "frontend_bytes": sum(p.stat().st_size for p in dist_dir.rglob("*") if p.is_file())}
    meta = {
        "product": "DataSwarm",
        "version": version,
        "source": f"{layout}-checkout",
        "upstream_base": {"ref": baseline_ref},
        "commit": head,
        "excludes": [e[len(":(exclude)"):] for e in EXCLUDES],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    print("[2/5] 构建 whl（导出树 + 前端 dist 注入 + python -m build + 自检）...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    whl_p = _build_whl(tree_ref, version, dist_dir, OUT_DIR)

    (OUT_DIR / f"dataswarm-{version}-release-manifest.json").write_text(
        json.dumps({**meta, "frontend": stats}, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[3/5] sha256 校验和...")
    sha_whl = _write_sha256(whl_p)

    print("\n[OK] 发布产物就绪（跨平台 whl）:")
    print(f"  {whl_p.name}  ({whl_p.stat().st_size:,} bytes)")
    print(f"  sha256: {sha_whl}")
    print("  安装: pip install <whl> -> dataswarm-init -> dataswarm-start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
