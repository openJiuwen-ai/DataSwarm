#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""verify_slimming —— 对比瘦身前后会话的 LLM 可见上下文体量与耗时。

用法:
    python scripts/verify_slimming.py <baseline_analysis_id> <new_analysis_id>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ARTIFACT_ROOT = Path.home() / ".jiuwenswarm" / "artifact" / "analysis"


def _load(analysis_id: str) -> dict:
    return json.loads((ARTIFACT_ROOT / analysis_id / "manifest.json").read_text(encoding="utf-8-sig"))


def _span_seconds(manifest: dict) -> float | None:
    lt = manifest.get("llm_trace") or []
    if len(lt) < 2:
        return None
    first = datetime.fromisoformat(lt[0]["at"])
    last = datetime.fromisoformat(lt[-1]["at"])
    return (last - first).total_seconds()


def _stdout_total(manifest: dict, step: str | None = None) -> int:
    return sum(int(e.get("stdout_chars") or 0) for e in manifest.get("stdout_trace") or []
               if step is None or e.get("step") == step)


def _knowledge_chars(manifest: dict) -> int:
    return len(json.dumps(manifest.get("knowledge") or {}, ensure_ascii=False))


def _max_gap(manifest: dict) -> tuple[float, str] | None:
    lt = manifest.get("llm_trace") or []
    best = None
    for a, b in zip(lt, lt[1:]):
        gap = (datetime.fromisoformat(b["at"]) - datetime.fromisoformat(a["at"])).total_seconds()
        if best is None or gap > best[0]:
            best = (gap, f'{a.get("step")}->{b.get("step")}')
    return best


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    base, new = _load(sys.argv[1]), _load(sys.argv[2])
    rows = [
        ("问题", base.get("question", "")[:30], new.get("question", "")[:30]),
        ("trace 跨度(s)", _span_seconds(base), _span_seconds(new)),
        ("stdout 合计(字符)", _stdout_total(base) or None, _stdout_total(new)),
        ("  context 步", _stdout_total(base, "context") or None, _stdout_total(new, "context")),
        ("  plan 步(单轮最大)", _stdout_total(base, "plan") or None, _stdout_total(new, "plan")),
        ("  execute/compile 步", _stdout_total(base, "execute") + _stdout_total(base, "compile")
         + _stdout_total(base, "execute-batch") + _stdout_total(base, "compile-batch") or None,
         _stdout_total(new, "execute") + _stdout_total(new, "compile")
         + _stdout_total(new, "execute-batch") + _stdout_total(new, "compile-batch")),
        ("manifest.knowledge(字符)", _knowledge_chars(base), _knowledge_chars(new)),
        ("最大轮间隔(s)", _max_gap(base), _max_gap(new)),
    ]
    print(f"{'指标':<24}{'基线(改前)':<34}{'本轮(改后)':<34}")
    for name, b, n in rows:
        print(f"{name:<24}{str(b):<34}{str(n):<34}")
    gate = new.get("report_gate") or {}
    print("\nreport_gate:", "ok" if gate.get("ok") else "FAIL",
          "| coverage:", gate.get("coverage"), "| status:", gate.get("status"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
