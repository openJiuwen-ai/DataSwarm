# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Audit a native analysis snapshot and its archived evidence without adapters.

Usage: python scripts/dw-ops/audit_analysis.py --snapshot /path/analysis.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jiuwenswarm.channels.web.dataagent.analysis.report_validation import report_gate  # noqa: E402


def audit(snapshot: Path) -> dict:
    state = json.loads(snapshot.read_text(encoding="utf-8"))
    errors = []
    if state.get("protocol_version") != 1:
        return {"ok": False, "errors": ["unsupported analysis protocol"]}
    for key, value in state["usage"].items():
        if value < 0 or (key in {"model_calls", "model_tokens"} and value > state["limits"]["max_" + key]):
            errors.append(f"budget exceeded: {key}")
    for task in state["tasks"].values():
        if not 0 <= task["rounds"] <= state["limits"]["max_task_rounds"]:
            errors.append("task round budget exceeded")
        result = task.get("result") or {}
        if task["status"] not in {"succeeded", "qualified"}:
            continue
        path = Path(result["artifact"])
        if not path.is_absolute():
            path = snapshot.parent / path
        if not path.is_file():
            errors.append(f"missing evidence: {path.name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != result.get("artifact_hash"):
            errors.append(f"evidence hash mismatch: {path.name}")
    spec_path = snapshot.parent / "report_spec.json"
    gate = None
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        gate = report_gate(spec, state)
        errors.extend(gate["errors"])
    else:
        errors.append("missing report_spec.json")
    return {"ok": not errors, "analysis_id": state["analysis_id"], "errors": errors,
            "usage": state["usage"], "task_count": len(state["tasks"]), "report_gate": gate}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit native analysis evidence and shared budgets")
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.snapshot)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
