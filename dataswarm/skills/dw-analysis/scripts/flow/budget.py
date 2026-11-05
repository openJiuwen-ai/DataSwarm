#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""budget.py —— 预算判定（Phase 2c 自 analysis_flow 迁入）。

tool_calls 的单一闸门在 cli_registry.main（protected 命令先检查后计数）；
本模块只做 per-component 判定：deadline/sql/explore/retry 与 open 模式
optional 降级语义（advisory 见 _work_allowed docstring）。
"""
from __future__ import annotations

from jiuwenswarm.channels.web.dataagent.analysis.open_analysis import CLOSED_MAX_COMPONENTS

from .manifest import _component, _planned, _skip, proc_elapsed_ms


# 预算（方案 P0 建议初值）——closed 成分上限单源于服务端 open_analysis，
# 其余兜底值与服务端 BUDGET_CAPS 同值（防漂移测试锁定）
MAX_COMPONENTS = CLOSED_MAX_COMPONENTS


MAX_EXPLORE_ROUNDS = 3


MAX_EXPLORE_ROUNDS_OPTIONAL = 1


MAX_RETRIES = 2


MAX_SQL_COUNT = 15


# 工具调用总数上限：LLM 轮次的可靠下界（每轮至少一次工具调用）——
# 主 Agent 的 LLM 调用数脚本侧数不到（平台层事件），tool_calls 是可硬执行的
# 真实计数（实测 123 次工具/65 次思考的会话中 analysis_flow 占多数）
MAX_TOOL_CALLS = 30


def _budget_error(manifest: dict, kind: str, component_id: str, limit: int, current: int) -> SystemExit:
    return SystemExit(
        f"[analysis_flow] 预算超限（{kind}）: component={component_id} "
        f"current={current} limit={limit}——请返回部分结果或向用户澄清，不要继续重试")


def _explore_round_limit(manifest: dict, component: dict) -> int:
    """探索轮上限：open 模式 optional 成分 1 轮，其余（required/closed）3 轮。

    SKILL.md 行为契约与服务端 budget cap 都是「required ≤3、optional ≤1」，
    此前运行时只有 flat 3 轮——optional facet 的预算纪律从未被强制执行。
    """
    if manifest.get("mode") == "open" and not component.get("required", True):
        return MAX_EXPLORE_ROUNDS_OPTIONAL
    return MAX_EXPLORE_ROUNDS


def _work_allowed(manifest: dict, component_id: str, *, sql: bool = False, explore: bool = False,
                  advisory: bool = False) -> bool:
    """开放分析的执行门禁；optional 超限降级，required 不静默跳过。

    tool_calls 预算的单一闸门在 main()（protected 命令先检查后计数）。
    执行路径（compile/execute/probe…）到达这里时本次调用已被 main 计数
    放行，不再重复判定；advisory=True（cmd_next 波次建议）按 main 的准入
    边界预判（tool_calls >= cap 即不再建议新工作），避免「next 放行、
    execute 被 main 拒」的空转轮——原判据 > 与 main 的 >= 恰在边界分歧。
    """
    if manifest.get("mode") != "open":
        return True
    planned = _planned(manifest, component_id)
    state = _component(manifest, component_id)
    if state["status"] == "skipped":
        return False
    if state["status"] in {"done", "failed", "clarify", "infeasible"}:
        raise SystemExit(f"[analysis_flow] 成分 {component_id} 已结束，请先修计划再执行")
    limits = manifest["plan"].get("budget") or {}
    reason = ""
    # 累计脚本执行时间（历史进程已记账 + 本次进程进行中）——不含编排层
    # LLM/工具往返轮耗（墙钟判据的缺陷见 flow.manifest._save docstring）
    elapsed = manifest["budget"].get("exec_ms", 0) + proc_elapsed_ms()
    if elapsed >= limits.get("deadline_ms", 90000):
        reason = "deadline_exhausted"
    elif sql and manifest["budget"].get("sql_count", 0) >= limits.get("max_sql", MAX_SQL_COUNT):
        reason = "sql_budget_exhausted"
    elif advisory and manifest["budget"].get("tool_calls", 0) >= limits.get("max_tool_calls", MAX_TOOL_CALLS):
        reason = "tool_budget_exhausted"
    elif explore:
        cap = (limits.get("max_explore_rounds_required", 3) if planned["required"]
               else limits.get("max_explore_rounds_optional", 1))
        if len(state["explore_rounds"]) >= cap:
            reason = "exploration_exhausted"
    if reason:
        if planned["required"]:
            raise SystemExit(f"[analysis_flow] required 成分 {component_id}: {reason}，返回部分结果或澄清")
        _skip(manifest, component_id, reason)
        return False
    if sql:
        states = manifest.get("components") or {}
        dependencies = [states.get(dep, {}).get("status") for dep in planned.get("depends_on") or []]
        if not planned["required"] and any(status in {"failed", "skipped", "clarify", "infeasible"}
                                            for status in dependencies):
            _skip(manifest, component_id, "dependency_unavailable")
            return False
        if any(status != "done" for status in dependencies):
            raise SystemExit(f"[analysis_flow] 成分 {component_id} 依赖尚未成功")
        if not planned["required"] and any(
            states.get(c["component_id"], {}).get("status") in {"planned", "retrying"}
            for c in manifest["plan"]["components"] if c["required"]
        ):
            raise SystemExit("[analysis_flow] required 成分优先；先完成核心成分，再执行 optional")
    return True
