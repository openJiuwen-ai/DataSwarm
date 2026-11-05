#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""token_report.py —— 按分析流程步骤汇总 LLM 上下文体量与 token 估算。

数据源：manifest 的 stdout_trace（每命令 stdout 字符数，恒开启）与 llm_trace
（in_chars/out_chars，ANALYSIS_LLM_TRACE=1 时采集）。token 为**字符数换算估算**
（中文 ~0.6-1 token/字符、ASCII/JSON ~4 字符/token 的混合近似），非平台实测——
精确 token 需启用 trajectory_ui 数据面（llm.call span 含 gen_ai.usage，本机
默认关闭），启用后可按时间窗 join 替换估算列。

用法:
    python scripts/token_report.py <analysis_id>            # 单场分析分步报表
    python scripts/token_report.py --since 2026-10-22       # 某日起全部会话汇总
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 分析产物根跟随数据根（JIUWENSWARM_DATA_DIR 优先，无 env 回退 ~/.jiuwenswarm），
# 与 dataagent/analysis/api.py 的 _artifact_root 同一目录——隔离形态下读基座
# 旧目录会恒空（2026-11-02 方案B 同批收口）。
_data_root = os.environ.get("JIUWENSWARM_DATA_DIR")
ARTIFACT_ROOT = (Path(_data_root) if _data_root else Path.home() / ".jiuwenswarm") / "artifact" / "analysis"

# 混合中文/JSON 内容的经验换算（方案 §0 实测口径：召回包/规范/素材均为中英混排）
CHARS_PER_TOKEN_ESTIMATE = 1.4


def _est_tokens(chars: int) -> int:
    return int(chars / CHARS_PER_TOKEN_ESTIMATE)


def _load_manifest(analysis_id: str) -> dict:
    path = ARTIFACT_ROOT / analysis_id / "manifest.json"
    if not path.exists():
        raise SystemExit(f"[token_report] manifest 不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _report_one(manifest: dict) -> dict:
    """单场分析：按步骤聚合 stdout_trace 与 llm_trace 体量。"""
    stdout_steps: dict[str, dict] = defaultdict(lambda: {"calls": 0, "stdout_chars": 0})
    for entry in manifest.get("stdout_trace") or []:
        bucket = stdout_steps[entry.get("step") or "?"]
        bucket["calls"] += 1
        bucket["stdout_chars"] += int(entry.get("stdout_chars") or 0)

    llm_steps: dict[str, dict] = defaultdict(lambda: {"records": 0, "in_chars": 0, "out_chars": 0})
    for entry in manifest.get("llm_trace") or []:
        bucket = llm_steps[entry.get("step") or "?"]
        bucket["records"] += 1
        bucket["in_chars"] += int(entry.get("in_chars") or 0)
        bucket["out_chars"] += int(entry.get("out_chars") or 0)

    steps = {}
    for step in sorted(set(stdout_steps) | set(llm_steps)):
        steps[step] = {**stdout_steps.get(step, {"calls": 0, "stdout_chars": 0}),
                       **llm_steps.get(step, {"records": 0, "in_chars": 0, "out_chars": 0})}
    total_stdout = sum(v["stdout_chars"] for v in steps.values())
    return {
        "analysis_id": manifest.get("analysis_id"),
        "question": manifest.get("question", ""),
        "steps": steps,
        "total_stdout_chars": total_stdout,
        "total_stdout_est_tokens": _est_tokens(total_stdout),
        "note": "token 为字符换算估算（非平台实测）；精确值需启用 trajectory_ui 数据面",
    }


def _print_report(report: dict) -> None:
    print(f"分析: {report['analysis_id']}  问题: {report['question']}")
    print(f"{'步骤':<14}{'命令数':>6}{'stdout字符':>12}{'估算token':>10}"
          f"{'LLM记录':>8}{'输入字符':>10}{'输出字符':>10}")
    for step, v in report["steps"].items():
        print(f"{step:<14}{v['calls']:>6}{v['stdout_chars']:>12}{_est_tokens(v['stdout_chars']):>10}"
              f"{v['records']:>8}{v['in_chars']:>10}{v['out_chars']:>10}")
    print(f"{'合计':<14}{'':>6}{report['total_stdout_chars']:>12}"
          f"{report['total_stdout_est_tokens']:>10}")
    print(f"注: {report['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="按分析流程步骤汇总 LLM 上下文体量与 token 估算")
    parser.add_argument("analysis_id", nargs="?", help="analysis_id（单场报表）")
    parser.add_argument("--since", help="日期（YYYY-MM-DD）：汇总该日起全部会话")
    args = parser.parse_args()
    if not args.analysis_id and not args.since:
        parser.error("需要 <analysis_id> 或 --since <YYYY-MM-DD>")

    if args.analysis_id:
        _print_report(_report_one(_load_manifest(args.analysis_id)))
        return 0

    # --since 汇总：跨会话按步骤累计（不同流程的体量对比口径）
    since = datetime.fromisoformat(args.since).isoformat(timespec="seconds")
    merged: dict[str, dict] = defaultdict(lambda: {"calls": 0, "stdout_chars": 0,
                                                   "records": 0, "in_chars": 0, "out_chars": 0})
    sessions = 0
    for manifest_path in sorted(ARTIFACT_ROOT.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if str(manifest.get("created_at") or "") < since:
            continue
        sessions += 1
        for step, v in _report_one(manifest)["steps"].items():
            bucket = merged[step]
            for key in ("calls", "stdout_chars", "records", "in_chars", "out_chars"):
                bucket[key] += v[key]
    if not sessions:
        raise SystemExit(f"[token_report] {args.since} 起无分析会话")
    total = sum(v["stdout_chars"] for v in merged.values())
    print(f"{args.since} 起 {sessions} 场分析 · 按步骤累计")
    print(f"{'步骤':<14}{'命令数':>6}{'stdout字符':>12}{'估算token':>10}"
          f"{'LLM记录':>8}{'输入字符':>10}{'输出字符':>10}")
    for step, v in sorted(merged.items()):
        print(f"{step:<14}{v['calls']:>6}{v['stdout_chars']:>12}{_est_tokens(v['stdout_chars']):>10}"
              f"{v['records']:>8}{v['in_chars']:>10}{v['out_chars']:>10}")
    print(f"{'合计':<14}{'':>6}{total:>12}{_est_tokens(total):>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
