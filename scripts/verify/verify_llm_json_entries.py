#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""审计 JSON 解析入口，按数据来源分类，人工确认是否需要 llm_json 保护。

背景：模型生成的 spec / plan / manifest 会类型漂移（数组写成字符串、单对象不套
数组），直接迭代会把内容逐字符拆条（如报告「结论摘要」只剩首字）。防护口径：

- 模型产物入口 → ``_dw_common/scripts/llm_json.py``（围栏/尾逗号/类型归一化）；
- 字段消费 → ``as_items`` / ``as_dicts`` / ``as_text_items``；
- 无法兼容的结构 → 校验层大声失败（带字段路径）。

classify：
  file  —— 读取磁盘 JSON（可能是模型/agent 写的 spec、plan、manifest）
  cli   —— 命令行参数 JSON（模型直接构造）
  stdio —— 子进程 stdout（多为服务端自产）
  other —— 其余

用法:
    python scripts/verify/verify_llm_json_entries.py [目录 ...]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_ROOTS = (
    "jiuwenswarm/resources/agent/workspace/skills",
    "jiuwenswarm/channels/web/dataagent",
)
PARSERS = ("json.loads", "json.load", "repair_json", "json_repair.loads")


def classify(node: ast.AST) -> str:
    """按解析入参的来源分类。"""
    source = ast.unparse(node)
    if "stdout" in source or "stderr" in source or "communicate" in source:
        return "stdio"
    if any(token in source for token in ("args.", "argv", "sys.stdin", "input(")):
        return "cli"
    if any(token in source for token in ("read_text", "read_bytes", "open(", "Path(")):
        return "file"
    return "other"


def scan(path: Path) -> list[tuple[str, str, int]]:
    """扫描单个文件中所有 JSON 解析调用。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    rows: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or ast.unparse(node.func) not in PARSERS:
            continue
        argument = node.args[0] if node.args else None
        rows.append((
            classify(argument) if argument is not None else "other",
            path.as_posix(),
            node.lineno,
        ))
    return rows


def main(argv: list[str]) -> int:
    roots = argv[1:] or list(DEFAULT_ROOTS)
    rows: list[tuple[str, str, int]] = []
    for root in roots:
        for path in Path(root).rglob("*.py"):
            if "__pycache__" not in path.parts:
                rows.extend(scan(path))
    rows.sort()

    counts = {category: sum(1 for row in rows if row[0] == category)
              for category in ("file", "cli", "stdio", "other")}
    print(f"共 {len(rows)} 个 JSON 解析入口：{counts}")
    for category in ("file", "cli", "other", "stdio"):
        matched = [row for row in rows if row[0] == category]
        if not matched:
            continue
        print(f"\n## {category}（{len(matched)}）")
        for _, path, line in matched:
            print(f"{path}:{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
