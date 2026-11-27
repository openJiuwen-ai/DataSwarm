# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-based thin client for the analysis task service; no local control loops."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .dwcli_client import _analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="分析任务：服务端预算、取数与有限恢复")
    parser.add_argument(
        "action",
        choices=[
            "start", "context", "submit-plan", "dispatch", "review-core", "fetch-prepare",
            "fetch-step", "fetch-run", "compute-results", "read-results", "status", "finish",
        ],
    )
    parser.add_argument("--input-file", required=True, help="JSON文件，或 - 从stdin读取")
    args = parser.parse_args()
    try:
        text = sys.stdin.read() if args.input_file == "-" else Path(args.input_file).read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        result = _analysis("analysis", "runtime", {"action": args.action, "payload": payload})
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
