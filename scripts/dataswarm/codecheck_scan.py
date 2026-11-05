#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""codecheck_scan.py —— 华为 CodeArts Check 本地引擎扫描封装（提交前门禁 / 全量巡检）。

复用已安装的 VSCode CodeCheck 插件本地引擎（codecheck-ide-engine.jar，
工具链 = pylint + flake8 + SecFinder-Py/bandit），按 MR 门禁对齐口径
（scripts/dataswarm/codecheck_rules.json，源自规则集 xlsx 的 104 条编号规则）
过滤结果。豁免语义是「规则×场景」：scene_waived 规则命中只标注不吞，
新引入的真问题不会被规则级豁免掩盖；systematic_fp 规则命中归误报组。

用法:
    python scripts/dataswarm/codecheck_scan.py                # diff 模式（工作区 vs HEAD 改动 + 未跟踪 .py）
    python scripts/dataswarm/codecheck_scan.py --full         # 全量（三目录全部 tracked .py）
    python scripts/dataswarm/codecheck_scan.py --base main    # diff 对比指定基线
    python scripts/dataswarm/codecheck_scan.py --dry-run     # 只列待扫描文件，不跑引擎

退出码: 需关注（未豁免、非误报）的致命/严重 >0 时 exit 1，供 smart-commit / CI 门禁。
环境变量 CODECHECK_TOOLS 可显式指定引擎 tools 目录（缺省自动探测）。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dataswarm_lib import REPO_ROOT, git  # noqa: E402

# 扫描范围：dev 全树用 jiuwenswarm/，delta 镜像仓用 dataswarm/（按检出内容自动探测）
_SCAN_ROOT = "dataswarm" if (REPO_ROOT / "dataswarm").is_dir() else "jiuwenswarm"
SCAN_SCOPES = (_SCAN_ROOT, "scripts", ".claude")
RULES_FILE = Path(__file__).resolve().parent / "codecheck_rules.json"
DEFAULT_TIMEOUT = 1800  # 单次引擎运行上限（秒），全量 386 文件约数分钟

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def resolve_tools_dir() -> Path:
    """定位 CodeCheck 引擎 tools 目录（环境变量优先，其次常见安装位探测）。"""
    env_dir = os.environ.get("CODECHECK_TOOLS")
    if env_dir:
        tools = Path(env_dir)
        if _engine_jar(tools).is_file():
            return tools
        raise SystemExit(f"[错误] CODECHECK_TOOLS={env_dir} 下无 codecheck-ide-engine.jar")

    for base in (Path("D:/huawei-codearts-check"), Path("C:/huawei-codearts-check")):
        if not base.is_dir():
            continue
        for app in sorted(base.iterdir()):
            tools = app / "CodeCheckIDE" / "tools"
            if _engine_jar(tools).is_file():
                return tools
    raise SystemExit(
        "[错误] 未找到 CodeCheck 本地引擎。请安装 VSCode 插件 huaweicloud.codecheck（CodeArts Check）"
        "并在 IDE 里跑一次检查完成引擎下载，或设 CODECHECK_TOOLS 指向引擎 tools 目录")


def _engine_jar(tools: Path) -> Path:
    return tools / "codecheck-ide-engine" / "codecheck-ide-engine.jar"


def _java_exe(tools: Path) -> str:
    """引擎自带 JDK 优先（版本匹配），找不到再退回系统 java。"""
    for cand in (tools / "huaweiJDK" / "bin" / "java.exe", tools / "huaweiJDK" / "bin" / "java"):
        if cand.is_file():
            return str(cand)
    return "java"


def _in_scope(repo_path: str) -> bool:
    return repo_path.endswith(".py") and any(repo_path.startswith(f"{s}/") for s in SCAN_SCOPES)


def collect_files(base: str | None, full: bool) -> list[str]:
    """收集待扫描文件（仓库相对路径）：全量 = tracked 全集；diff = 改动 + 未跟踪。"""
    if full:
        out = git("ls-files", "-z").stdout
        tracked = [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]
        return sorted(p for p in tracked if _in_scope(p))

    changed: set[str] = set()
    diff = git("diff", "--name-only", "-z", base or "HEAD", check=False)
    changed.update(p.decode("utf-8", "replace") for p in diff.stdout.split(b"\0") if p)
    others = git("ls-files", "--others", "--exclude-standard", "-z", check=False)
    changed.update(p.decode("utf-8", "replace") for p in others.stdout.split(b"\0") if p)
    return sorted(p for p in changed if _in_scope(p))


def run_engine(tools: Path, files: list[str], report_dir: Path, timeout: int) -> bool:
    """调用引擎检查，产物落 report_dir（defects.json / toolReport / engine.log）。"""
    proj_root = str(REPO_ROOT).replace("\\", "/")
    payload = {
        "projectRoot": proj_root,
        "sourceFiles": [f"{proj_root}/{p}" for p in files],
        "sourceDirs": [],
        "exclude": "",
    }
    src_file = report_dir / "checkPath" / "source.txt"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(json.dumps(payload), encoding="utf-8")

    cmd = [
        _java_exe(tools), "-jar", str(_engine_jar(tools)),
        "--repo", "", "--branch", "", "--user", getpass.getuser(),
        "--report-output-dir", str(report_dir).replace("\\", "/"),
        "--check-config-file", str(src_file).replace("\\", "/"),
        "--lang", "en",
    ]
    with open(report_dir / "engine.log", "wb") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if proc.returncode != 0:
        print(f"[错误] 引擎退出码 {proc.returncode}，详见 {report_dir / 'engine.log'}", file=sys.stderr)
        return False
    if not (report_dir / "defects.json").is_file():
        print(f"[错误] 引擎未产出 defects.json，详见 {report_dir / 'engine.log'}", file=sys.stderr)
        return False
    return True


def load_defects(report_dir: Path) -> list[dict]:
    data = json.loads((report_dir / "defects.json").read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _rule_code(criterion_name: str) -> str | None:
    m = re.match(r"(G\.[A-Z]+\.\d+)", criterion_name or "")
    return m.group(1) if m else None


def build_report(defects: list[dict], rules: dict[str, dict]) -> tuple[dict, int]:
    """按对齐口径分组缺陷。返回 (分组结果, 需关注致命+严重条数)。"""
    groups: dict[str, list[dict]] = {"focus": [], "scene": [], "fp": [], "outside": []}
    for d in defects:
        code = _rule_code(d.get("criterionName", ""))
        meta = rules.get(code)
        if meta is None:
            groups["outside"].append(d)
            continue
        entry = {
            "code": code, "level": meta["level"], "desc": meta["desc"],
            "file": d.get("buggyFilePath", ""), "row": d.get("startRowNumber", 0),
            "message": (d.get("description") or "").strip(),
            "note": meta.get("scene_waived") or meta.get("systematic_fp") or "",
        }
        if "systematic_fp" in meta:
            groups["fp"].append(entry)
        elif "scene_waived" in meta:
            groups["scene"].append(entry)
        else:
            groups["focus"].append(entry)
    outside = []
    for d in groups["outside"]:
        outside.append({
            "code": _rule_code(d.get("criterionName", "")) or "无编号",
            "level": "-", "desc": d.get("criterionName", ""),
            "file": d.get("buggyFilePath", ""), "row": d.get("startRowNumber", 0),
            "message": (d.get("description") or "").strip(), "note": "口径外（--all-rules 才展示）",
        })
    groups["outside"] = outside

    focus_severe = sum(1 for e in groups["focus"] if e["level"] in ("致命", "严重"))
    return groups, focus_severe


def print_report(groups: dict[str, list[dict]], files_n: int, total: int, report_dir: Path,
                 out_json: Path | None, all_rules: bool) -> None:
    def rel(p: str) -> str:
        return p.replace("\\", "/").replace(f"{REPO_ROOT.as_posix()}/", "")

    print(f"\n=== CodeArts Check 本地扫描报告（报告目录: {report_dir}）===")
    print(f"扫描文件: {files_n} | 引擎报出: {total} | 对齐口径内: "
          f"{len(groups['focus']) + len(groups['scene']) + len(groups['fp'])}")

    for key, title in (("focus", "需关注"), ("scene", "场景豁免标注（对照 docs/scan-waivers.md 定性，不阻断）"),
                       ("fp", "系统性误报（本仓库检测模式失效，已核实）")):
        items = groups[key]
        if not items:
            continue
        print(f"\n-- {title}: {len(items)} 条 --")
        by_code: dict[str, list[dict]] = {}
        for e in items:
            by_code.setdefault(e["code"], []).append(e)
        for code, hits in sorted(by_code.items(), key=lambda kv: (-len(kv[1]))):
            first = hits[0]
            print(f"[{first['level']}] {code} {first['desc']} ×{len(hits)}")
            for e in hits[:15]:
                print(f"    {rel(e['file'])}:{e['row']}  {e['message'][:90]}")
            if len(hits) > 15:
                print(f"    ... 其余 {len(hits) - 15} 条")
            if key != "focus" and first["note"]:
                print(f"    > {first['note']}")

    if groups["outside"]:
        if all_rules:
            print(f"\n-- 口径外（引擎全量规则库，不在 MR 门禁 104 规则内）: {len(groups['outside'])} 条 --")
            by_code: dict[str, list[dict]] = {}
            for e in groups["outside"]:
                by_code.setdefault(e["code"], []).append(e)
            for code, hits in sorted(by_code.items(), key=lambda kv: (-len(kv[1]))):
                print(f"[口径外] {code} {hits[0]['desc']} ×{len(hits)}")
                for e in hits[:5]:
                    print(f"    {rel(e['file'])}:{e['row']}  {e['message'][:90]}")
        else:
            print(f"\n-- 口径外（引擎全量规则库，不在 MR 门禁 104 规则内）: {len(groups['outside'])} 条 "
                  f"(加 --all-rules 展示) --")

    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(groups, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[JSON 结果已导出] {out_json}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="CodeArts Check 本地扫描（默认 diff 模式；对齐口径见 codecheck_rules.json）")
    ap.add_argument("--full", action="store_true", help="全量扫描三目录全部 tracked .py（默认只扫改动）")
    ap.add_argument("--base", default="HEAD", help="diff 模式的对比基线（默认 HEAD）")
    ap.add_argument("--report-dir", type=Path, default=None, help="引擎输出目录（默认引擎盘 scan-<时间戳>）")
    ap.add_argument("--json", type=Path, default=None, help="过滤后结果导出 JSON 路径")
    ap.add_argument("--all-rules", action="store_true", help="展示口径外（全量规则库）结果")
    ap.add_argument("--dry-run", action="store_true", help="只列待扫描文件，不调引擎")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"引擎超时秒数（默认 {DEFAULT_TIMEOUT}）")
    args = ap.parse_args()

    manifest = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    rules = manifest["rules"]

    files = collect_files(args.base, args.full)
    mode = "full" if args.full else f"diff (base={args.base})"
    print(f"[*] 模式: {mode} | 待扫描: {len(files)} 个文件")
    if not files:
        print("[*] 无待扫描文件，结束")
        return 0
    if args.dry_run:
        for p in files:
            print("   ", p)
        return 0

    tools = resolve_tools_dir()
    report_dir = args.report_dir or (tools.parent.parent / f"scan-{time.strftime('%Y%m%d%H%M%S')}")
    report_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] 引擎: {_engine_jar(tools)}")
    print("[*] 运行引擎（可能需要数分钟）...")
    if not run_engine(tools, files, report_dir, args.timeout):
        return 2

    defects = load_defects(report_dir)
    groups, focus_severe = build_report(defects, rules)
    print_report(groups, len(files), len(defects), report_dir, args.json, args.all_rules)

    if focus_severe:
        print(f"\n[✗] 需关注致命/严重 {focus_severe} 条，门禁不通过（exit 1）")
        return 1
    print("\n[✓] 需关注致命/严重为 0，门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
