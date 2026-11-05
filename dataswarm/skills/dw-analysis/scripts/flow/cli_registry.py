#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""cli_registry.py —— 声明式子命令注册表 + main（Phase 2c 自 analysis_flow 迁入）。

COMMANDS 常量导出全部子命令名（防漂移测试消费：命令清单 ↔ SKILL.md 文档面）。
main 的 tool_calls 熔断是全脚本唯一闸门（先检查后计数，protected 集合见下）。
"""
from __future__ import annotations

import argparse
import sys

from . import commands as _cmds
from .budget import MAX_TOOL_CALLS
from .manifest import _component, _load, _manifest_lock, _save, _skip

COMMANDS = (
    "init", "meta", "context", "plan", "trace",
    "compile", "compile-batch", "check", "check-batch",
    "execute-batch", "execute", "probe", "join",
    "list-tables", "describe", "result-check", "manifest",
    "next", "skip", "report-gate",
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="analysis_flow.py",
                                     description="dw-analysis 四层链路确定性 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="建分析会话")
    p.add_argument("--question", required=True)
    p.add_argument("--meta-file",
                   help="intent_normalized JSON 文件（Step 0.5 产物；校验后入 manifest）")
    p.set_defaults(func=_cmds.cmd_init)

    p = sub.add_parser("meta", help="改判留痕：覆盖 intent_normalized（Step 3 就地改判）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--meta-file", required=True,
                   help="改判后的 intent_normalized JSON 文件（schema 同 init，校验一致）")
    p.set_defaults(func=_cmds.cmd_meta)

    p = sub.add_parser("context", help="L1+L2 上下文（mode=recall_more 记探索轮）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--mode", default="initial", choices=["initial", "recall_more"])
    p.add_argument("--component-id", default="")
    p.add_argument("--source-id", default="")
    p.set_defaults(func=_cmds.cmd_context)

    p = sub.add_parser("plan", help="QueryPlan 程序校验")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--plan-file", default=None)
    p.add_argument("--plan-json", default=None)
    p.add_argument("--clarified", default=None,
                   help="用户已确认的口径说明（歧义消解标记，clarify gate 放行条件）")
    p.set_defaults(func=_cmds.cmd_plan)

    p = sub.add_parser("trace", help="记录模型判断或探索动作（--traces-json 支持批量）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", default="")
    p.add_argument("--decision", default=None,
                   choices=[None, "sufficient", "explore", "clarify", "infeasible"])
    p.add_argument("--missing-file", default=None)
    p.add_argument("--missing-json", default=None)
    p.add_argument("--tool", default=None)
    p.add_argument("--summary", default="")
    p.add_argument("--traces-file", default=None,
                   help="批量形态：[{component_id, decision, missing, tool, summary}] 数组文件")
    p.add_argument("--traces-json", default=None, help="批量形态的 JSON 内联")
    p.set_defaults(func=_cmds.cmd_trace)

    p = sub.add_parser("compile", help="G1 编译通道（有选定指标的成分，零 LLM）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--source-id", default="")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=_cmds.cmd_compile)

    p = sub.add_parser("compile-batch", help="批量 G1 编译（多成分一轮提交，省逐成分往返）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--batch-json", default="")
    p.add_argument("--batch-file", default="")
    p.set_defaults(func=_cmds.cmd_compile_batch)

    p = sub.add_parser("check", help="scope 归属 + 口径一致性 + G 等级")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--sql-file", default=None)
    p.add_argument("--sql", default=None)
    p.add_argument("--selected-file", default=None)
    p.add_argument("--selected-json", default=None)
    p.add_argument("--temp-scope-file", default=None)
    p.add_argument("--temp-scope-json", default=None)
    p.add_argument("--time-json", default=None)
    p.set_defaults(func=_cmds.cmd_check)

    p = sub.add_parser("check-batch", help="批量检查（无依赖成分并行 2）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--batch-file", default=None,
                   help="[{component_id, sql, selected?, temp_scope?, time?}] 数组文件")
    p.add_argument("--batch-json", default=None, help="批量数组 JSON 内联")
    p.set_defaults(func=_cmds.cmd_check_batch)

    p = sub.add_parser("execute-batch", help="批量执行（并行 2 + 逐项结果检查）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--batch-file", default=None,
                   help="[{component_id, sql, source_id?, selected?, temp_scope?, time?}] 数组文件")
    p.add_argument("--batch-json", default=None, help="批量数组 JSON 内联")
    p.set_defaults(func=_cmds.cmd_execute_batch)

    p = sub.add_parser("execute", help="统一执行 + CSV + manifest 累积")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--sql-file", default=None)
    p.add_argument("--sql", default=None)
    p.add_argument("--source-id", default="")
    p.add_argument("--selected-file", default=None)
    p.add_argument("--selected-json", default=None)
    p.add_argument("--temp-scope-file", default=None)
    p.add_argument("--temp-scope-json", default=None)
    p.add_argument("--time-json", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=_cmds.cmd_execute)

    p = sub.add_parser("probe", help="维值探查（受限 DISTINCT/LIKE；--targets 批量算一轮）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--table", default="")
    p.add_argument("--column", default="")
    p.add_argument("--targets", default="",
                   help='批量目标 "表.列,表.列"——整批算一轮探索（待定过滤值一轮确认完）')
    p.add_argument("--like", default="")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=_cmds.cmd_probe)

    p = sub.add_parser("join", help="join 校验（类型匹配 + 可选包含率）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--left", required=True, help="表.列")
    p.add_argument("--right", required=True, help="表.列")
    p.add_argument("--sample", action="store_true")
    p.set_defaults(func=_cmds.cmd_join)

    p = sub.add_parser("list-tables", help="活库表清单（限授权 source）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", default="main")
    p.add_argument("--db-ref", default="")
    p.set_defaults(func=_cmds.cmd_list_tables)

    p = sub.add_parser("describe", help="活库表结构（限授权 source）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", default="main")
    p.add_argument("--table", required=True)
    p.add_argument("--db-ref", default="")
    p.set_defaults(func=_cmds.cmd_describe)

    p = sub.add_parser("result-check", help="执行后结果检查（补跑）")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--input-file", default=None)
    p.add_argument("--input-json", default=None)
    p.set_defaults(func=_cmds.cmd_result_check)

    p = sub.add_parser("manifest", help="输出当前 manifest")
    p.add_argument("--analysis-id", required=True)
    p.set_defaults(func=_cmds.cmd_manifest)

    p = sub.add_parser("next", help="获取下一波可执行成分（required 优先）")
    p.add_argument("--analysis-id", required=True)
    p.set_defaults(func=_cmds.cmd_next)

    p = sub.add_parser("skip", help="显式跳过 optional 侧面并记录原因")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--component-id", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmds.cmd_skip)

    p = sub.add_parser("report-gate", help="校验开放分析报告的证据引用与核心覆盖")
    p.add_argument("--analysis-id", required=True)
    p.add_argument("--spec-file", required=True)
    p.set_defaults(func=_cmds.cmd_report_gate)

    args = parser.parse_args()
    try:
        if args.command == "init":
            return args.func(args)
        # 跨进程账本锁：编排层可能一轮并行发起多个 analysis_flow 进程（G1
        # 通道并行 compile 多成分），main 层计账与子命令的 load→改→save
        # 全程互斥，防丢失更新（见 _manifest_lock docstring）。
        with _manifest_lock(args.analysis_id):
            manifest = _load(args.analysis_id)
            cap = ((manifest.get("plan") or {}).get("budget") or {}).get("max_tool_calls", MAX_TOOL_CALLS)
            # 执行前熔断，并把失败调用也记账；观测/交付/显式降级不受该上限阻断。
            protected = {"execute", "execute-batch", "compile", "check", "check-batch",
                         "probe", "join", "list-tables", "describe", "context", "plan"}
            if manifest.get("budget", {}).get("tool_calls", 0) >= cap and args.command in protected:
                if manifest.get("mode") == "open":
                    for planned in manifest["plan"]["components"]:
                        state = _component(manifest, planned["component_id"])
                        if not planned["required"] and state["status"] in {"planned", "retrying"}:
                            _skip(manifest, planned["component_id"], "tool_budget_exhausted")
                _save(manifest)
                raise SystemExit("[analysis_flow] 工具调用预算耗尽，请返回部分结果或澄清")
            manifest.setdefault("budget", {})["tool_calls"] = manifest.get("budget", {}).get("tool_calls", 0) + 1
            _save(manifest)
            return args.func(args)
    except RuntimeError as exc:  # dwcli/服务错误 → exit 2
        print(f"[analysis_flow] {exc}", file=sys.stderr)
        return _cmds.EXIT_SERVICE
    except SystemExit as exc:
        print(f"{exc}", file=sys.stderr)
        return _cmds.EXIT_USAGE
