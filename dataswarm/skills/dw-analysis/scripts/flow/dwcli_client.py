#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dwcli_client.py —— dwcli 子进程封装（skill 侧服务端调用唯一出口）。

从 analysis_flow.py 抽出（Phase 2a 纯搬运，行为零变化）。失败语义见
_dwcli docstring：dwcli 的错误详情在 stdout JSON 的 error/detail 字段。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DWCLI_TIMEOUT = int(os.environ.get("DWCLI_TIMEOUT_S", "600"))


def _dwcli(args: list[str], *, json_body: dict | None = None) -> dict:
    """调 dwcli；json_body 经 stdin 传给 --json-file -。"""
    cmd = [sys.executable, "-m", "jiuwenswarm.cli.dwcli", *args]
    stdin_text = json.dumps(json_body, ensure_ascii=False) if json_body is not None else None
    try:
        proc = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=DWCLI_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"dwcli 超时（{DWCLI_TIMEOUT}s）") from e
    if proc.returncode != 0:
        # The structured stdout carries the server error, stderr may contain environment notes.
        try:
            err = json.loads(proc.stdout)
            parts = [str(err.get(k)) for k in ("error", "detail") if err.get(k)]
            raise RuntimeError("dwcli: " + (" | ".join(parts) or f"exit {proc.returncode}"))
        except json.JSONDecodeError as exc:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"dwcli exit {proc.returncode}") from exc
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"dwcli 输出非法 JSON: {proc.stdout[:200]}") from e


def _analysis(group: str, action: str, body: dict) -> dict:
    return _dwcli([group, action, "--json-file", "-"], json_body=body)
