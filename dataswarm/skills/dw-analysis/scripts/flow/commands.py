#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""commands.py —— 20 个子命令实现（Phase 2c 自 analysis_flow 迁入）。

组合 flow 各层：dwcli_client（服务端调用）/ manifest（账本）/ budget（判定）/
execute_core（执行管线）/ observability（打点与 hints）。stdout 契约与
调用形态不变（golden 测试锁定）。

对 dwcli/manifest 经模块属性调用（``_dc``/``_mm``）——测试打桩 seam。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from jiuwenswarm.channels.web.dataagent.analysis.open_analysis import (
    OPEN_MAX_COMPONENTS,
    report_gate,
)

from . import dwcli_client as _dc
from . import manifest as _mm
from .batch import BATCH_PARALLELISM, _run_batch
from .budget import (MAX_COMPONENTS, MAX_RETRIES, MAX_SQL_COUNT, _budget_error,
                     _explore_round_limit, _work_allowed)
from .execute_core import _sql_hash, record_execution
from .manifest import _component, _load, _now, _planned, _save, _skip
from .observability import (_context_hints, _emit, _execute_hints,
                            _llm_execute_context, _plan_row_hints,
                            _record_llm_step, _report_gate_hints)


EXIT_USAGE, EXIT_SERVICE = 1, 2

INTENT_VALUES = ("lookup", "report_only", "computation", "exploration", "analysis", "diagnosis")


def _open_analysis_enabled() -> bool:
    """平台开关 open_analysis_enabled（config.yaml 顶层，默认开）。

    与服务端共用 jiuwenswarm.common.config 的 getter（解析语义零漂移）；
    函数内延迟 import + 失败按关闭降级（fail-closed）——顶层 import 意外
    失败不会拖垮 closed 主路。skill 每次调用都是新进程，读到的总是最新配置。
    """
    try:
        from jiuwenswarm.common.config import get_open_analysis_enabled
        return get_open_analysis_enabled()
    except Exception:
        return False


def _load_intent_meta(meta_file: str | None) -> dict | None:
    """读 Step 0.5 意图规范化产物 intent_normalized（五字段 JSON 文件）。

    Args:
        meta_file: JSON 文件路径；None/空串表示未传（向后兼容，manifest 不含该键）。

    Returns:
        校验通过的 meta dict；未传时 None。

    Raises:
        SystemExit: 文件不可读/非法 JSON/缺 question_raw/intent 不在六值枚举。
    """
    if not meta_file:
        return None
    try:
        meta = json.loads(Path(meta_file).read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"[analysis_flow] --meta-file 读取失败: {meta_file}（{e}）") from e
    if not isinstance(meta, dict) or not str(meta.get("question_raw") or "").strip():
        raise SystemExit("[analysis_flow] --meta-file 缺 question_raw（用户原话，必填）")
    if meta.get("intent") not in INTENT_VALUES:
        raise SystemExit(f"[analysis_flow] --meta-file intent 非法: {meta.get('intent')!r}"
                         f"（合法值: {'|'.join(INTENT_VALUES)}）")
    return meta


def cmd_init(args: argparse.Namespace) -> int:
    meta = _load_intent_meta(args.meta_file)
    open_enabled = _open_analysis_enabled()
    analysis_id = f"analysis-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
    manifest = {
        "analysis_id": analysis_id,
        "question": args.question,
        "created_at": _now(),
        "semantic_versions": {},
        "context": None,
        "plan": None,
        "components": {},
        "mode": "closed",
        "open_analysis_enabled": open_enabled,
        "facets": [],
        "evidence": [],
        "report_gate": None,
        "started_at": _now(),
        "budget": {"sql_count": 0, "explore_count": 0,
                   # 不记 llm_calls：LLM 调用发生在编排层模型侧，脚本不可观测
                   # （历史死字段已删）；实际计数由评测 run_case 逐轮统计承担
                   "tool_calls": 0},
        "uncovered": [],
        "result_checks": {},
        "notes": [],
    }
    _record_llm_step(manifest, "init", "deterministic",
                     context_digest=f"分析开始 · 问句: {args.question}")
    if meta:
        manifest["intent_normalized"] = meta
    _save(manifest)
    out = {"analysis_id": analysis_id, "manifest_path": str(_mm._manifest_path(analysis_id)),
           "question": args.question, "open_analysis_enabled": open_enabled}
    if meta:
        out["intent"] = meta["intent"]
    _emit(out)
    return 0


def cmd_meta(args: argparse.Namespace) -> int:
    """Step 3 就地改判留痕：覆盖 manifest.intent_normalized（设计 §5.2）。

    改判前的旧值追加进 intent_revisions——初判→终判的审计链不断，观察期
    分类准确率按初判口径统计不被覆盖污染。
    """
    meta = _load_intent_meta(args.meta_file)
    manifest = _load(args.analysis_id)
    revised_from = manifest.get("intent_normalized", {}).get("intent")
    if revised_from is not None:
        manifest.setdefault("intent_revisions", []).append(
            {"at": _now(), "intent": revised_from})
    manifest["intent_normalized"] = meta
    _save(manifest)
    out = {"analysis_id": args.analysis_id, "intent": meta["intent"]}
    if revised_from is not None:
        out["revised_from"] = revised_from
    _emit(out)
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    if args.mode == "recall_more":
        if not _explore_allowed(manifest, args.component_id):
            return 0
        component = _component(manifest, args.component_id or "main")
        rounds = len(component["explore_rounds"])
        limit = _explore_round_limit(manifest, component)
        if rounds >= limit:
            raise _budget_error(manifest, "explore_rounds", args.component_id,
                                limit, rounds)
        component["explore_rounds"].append(
            {"tool": "recall_more", "question": args.question, "at": _now()})
        manifest["budget"]["explore_count"] += 1
    # getattr 兼容：三级漏斗上游测试构造的最小 Args 不带 source_id
    source_id = getattr(args, "source_id", "") or ""
    result = _dc._analysis("analysis", "context", {
        "question": args.question, "mode": args.mode,
        "source_ids": [source_id] if source_id else []})
    # d5 软提示：侦查接近触顶（最后一轮）——实测侦查 10+ 轮浪费；提示在
    # 硬停（下一轮 SystemExit）之前一轮给出，给 LLM 转向的机会
    if args.mode != "initial" and rounds == limit - 1:
        result["hint"] = (f"[hint] 侦查已 {rounds + 1}/{limit} 轮，下一轮将触顶硬停——"
                          "信息足够请转入计划/执行，不足则合并剩余疑问一次问清")
    if args.mode == "initial":
        manifest["context"] = result
        manifest["semantic_versions"] = result.get("semantic_versions") or {}
        manifest["knowledge_retrieval"] = result.get("knowledge_retrieval") or {}
    else:
        _component(manifest, args.component_id or "main")["context"] = result
    # 漏斗路由入账本（评审要能回答「这个问题为什么没走短路」）
    manifest["route"] = result.get("route") or ""
    manifest["route_tier"] = result.get("route_tier") or ""
    manifest["route_reasons"] = list(result.get("route_reasons") or [])
    if result.get("scale_gate") and (args.mode == "initial" or not manifest.get("scale_gate")):
        manifest["scale_gate"] = result["scale_gate"]
    elif result.get("scale_gate"):
        component.setdefault("scale_gate_history", []).append(result["scale_gate"])
    if result.get("shortcut"):
        manifest["shortcut"] = result["shortcut"]
    if args.mode == "recall_more":
        _record_llm_step(manifest, "context", "decision",
                         component_id=args.component_id or None,
                         ai_output=args.question,
                         context_digest="补召回轮：模型基于缺项选择再召回方向",
                         context_payload={"sufficiency_trace": component["sufficiency_trace"][-2:],
                                          "attempts": component["attempts"][-3:]},
                         refs=[f"manifest.components.{args.component_id or 'main'}"])
    else:
        packs = result.get("packs") or {}
        _record_llm_step(manifest, "context", "deterministic",
                         context_digest=(
                             f"route={result.get('route') or '—'} · 候选 "
                             f"{len(packs.get('metrics_candidates') or [])} 指标/"
                             f"{len(packs.get('schema_tables') or [])} 表/"
                             f"{len(packs.get('glossary') or [])} 术语 · 歧义 "
                             f"{len(result.get('ambiguities') or [])}"),
                         refs=["manifest.context"])
    _save(manifest)
    hints = _context_hints(manifest)
    if hints:
        result["hints"] = hints
    _emit(result, manifest, "context")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    # clarify gate（F-009 架构级）：context 报告的歧义未消解前禁止出计划——
    # 缺业务口径只能向用户澄清，禁止猜口径直接算。用户确认后带 --clarified 重提。
    clarified = getattr(args, "clarified", None)
    if clarified:
        manifest.setdefault("clarifications", []).append(
            {"text": str(clarified), "at": _now()})
        _save(manifest)
    ambiguities = (manifest.get("context") or {}).get("ambiguities") or []
    if ambiguities and not manifest.get("clarifications"):
        summary = "; ".join(
            str(a.get("term") or a.get("text") or a) for a in ambiguities[:3])
        raise SystemExit(
            "[analysis_flow] 存在未消解歧义（" + summary + "）——缺业务口径只能向用户澄清，"
            "禁止猜口径。向用户提问并在其确认后，带 --clarified \"<用户确认的口径>\" 重新提交 plan")
    payload = _read_json_arg(args.plan_file, args.plan_json)
    if not isinstance(payload, dict):
        raise SystemExit("[analysis_flow] plan 必须是 JSON 对象")
    if str(payload.get("mode") or "closed").lower() == "open" and not _open_analysis_enabled():
        raise SystemExit(
            "[analysis_flow] 开放分析未启用（open_analysis_enabled=false），mode=open "
            "计划被拒绝——请改用 mode=closed（≤5 个成分）重新提交；如确需开放分析，"
            "由平台管理员在 ~/.dataswarm/config/config.yaml 顶层设置 "
            "open_analysis_enabled: true 后重试（已建 open 会话的执行/report-gate "
            "不受影响，仅重新 plan 会被拦）")
    plan = {**payload, "analysis_id": args.analysis_id,
            "question": payload.get("question") or manifest["question"]}
    components = plan.get("components") or []
    max_components = OPEN_MAX_COMPONENTS if plan.get("mode") == "open" else MAX_COMPONENTS
    if len(components) > max_components:
        raise SystemExit(f"[analysis_flow] component 数 {len(components)} 超上限 "
                         f"{max_components}——请拆批或收窄问题")
    candidates = _candidate_keys(manifest)
    selected_keys = sorted({m for c in components for m in (c.get("selected_metrics") or [])})
    _record_llm_step(manifest, "plan", "generation", ai_output=payload,
                     context_digest=(f"route={manifest.get('route') or '—'} · 候选 {len(candidates)} 指标 · "
                                     f"歧义 {len(ambiguities)}"),
                     context_payload=manifest.get("context"),
                     refs=["manifest.context", "manifest.plan_raw"])
    result = _dc._analysis("analysis", "plan", {"plan": plan, "candidates": candidates})
    if not result.get("validation", {}).get("ok"):
        _save(manifest)
        # 错误摘要前置：完整 result 在下方 stdout，修正依据直接可读，
        # 无需翻找输出文件（实测模型曾为定位 errors 连做 read/grep 四轮）。
        errors = (result.get("validation") or {}).get("errors") or []
        print(f"[analysis_flow] 计划校验未通过——{len(errors)} 条错误，逐条修正后重提（≤2 次）：",
              file=sys.stderr)
        for err in errors[:8]:
            field = err.get("component_id") or err.get("field") or "?"
            print(f"  - [{field}] {err.get('message') or err}", file=sys.stderr)
        result["hints"] = [
            "计划校验未通过——按 errors 逐条修正后重提（≤2 次）；metric 未选定的成分"
            "回 context 重选指标走编译通道，不要手写 G2 硬凑；越界成分按 fix_plan 拆分"
            "或取消选定转探索",
        ]
        _emit(result, manifest, "plan")
        return EXIT_USAGE
    previous_plan = manifest.get("plan") or {}
    manifest["plan"] = result.get("plan")
    if result.get("knowledge"):
        manifest["knowledge"] = result["knowledge"]
    if previous_plan and previous_plan != manifest["plan"]:
        manifest.setdefault("plan_history", []).append({"plan": previous_plan, "at": _now()})
        # 修计划后逐成分比对：内容逐字未变的成分保留执行状态与证据，变化
        # 成分失效重执行（原全量失效让未变成分陪跑——smoke 实证 c1 未变
        # 仍被重新编译执行，SQL 与首次完全相同）。dict 全等即保留：任何
        # 字段（含未来新增）有差异都按变化处理，不靠字段白名单。attempts
        # 审计保留。
        prev_components = {c.get("component_id"): c
                           for c in previous_plan.get("components") or []}
        next_components = {c.get("component_id"): c
                           for c in manifest["plan"].get("components") or []}
        for cid, state in manifest["components"].items():
            if prev_components.get(cid) == next_components.get(cid):
                continue
            state.update(status="planned", retries=0, explore_rounds=[])
            state.pop("evidence_id", None)
            state.pop("skip_reason", None)
    # 原始提交留档（含 sql_draft——规范回显不保留未知字段）
    manifest["plan_raw"] = payload
    manifest["uncovered"] = list((result.get("plan") or {}).get("uncovered") or [])
    manifest["mode"] = manifest["plan"].get("mode", "closed")
    manifest["facets"] = manifest["plan"].get("facets") or []
    manifest.setdefault("evidence", [])
    manifest.setdefault("started_at", _now())
    for planned in manifest["plan"].get("components") or []:
        state = _component(manifest, planned["component_id"])
        for key in ("role", "facet_id", "required", "priority"):
            state[key] = planned.get(key)
    # 计划选定指标 → 回读依赖闭包（L2③ metrics_selected）
    # 计划选定指标 → 依赖闭包（短路路径 context 已回填完整闭包，保留不覆盖）
    if selected_keys:
        packs = (manifest.get("context") or {}).get("packs") or {}
        known = {m.get("metric_key"): m for m in (packs.get("metrics_selected") or [])}
        known.update({m.get("metric_key"): m for m in (result.get("metrics_selected") or [])})
        packs["metrics_selected"] = [
            known.get(key) or {"metric_key": key} for key in selected_keys]
    _save(manifest)
    row_hints = _plan_row_hints(manifest)
    if row_hints:
        result["hints"] = row_hints
    _emit(result, manifest, "plan")
    return 0


def _candidate_keys(manifest: dict) -> list[str]:
    """计划可选的指标候选（去重保序）。

    完整召回路径取候选榜；**短路路径没有候选榜**——链指标在
    ``metrics_selected``，同样是合法候选（服务端对每个 key 回源复验
    published，客户端清单只是提示，列进来不构成扩权）。
    """
    packs = (manifest.get("context") or {}).get("packs") or {}
    keys = [c.get("metric_key") for c in packs.get("metrics_candidates") or []]
    keys += [m.get("metric_key") for m in packs.get("metrics_selected") or []]
    return [key for key in dict.fromkeys(keys) if key]


def cmd_trace(args: argparse.Namespace) -> int:
    """记录模型判断（sufficiency_trace）或探索动作（list_tables/describe 等）。

    批量形态（多成分一轮记完，省对话轮次）：
    ``trace --analysis-id <id> --traces-json '[{"component_id":"c1","decision":"sufficient"},...]'``
    """
    manifest = _load(args.analysis_id)
    if args.traces_json or args.traces_file:
        batch = _read_json_arg(args.traces_file, args.traces_json)
        if not isinstance(batch, list):
            raise SystemExit("[analysis_flow] --traces-json/--traces-file 必须是数组")
        emitted = []
        for item in batch:
            if not isinstance(item, dict) or not item.get("component_id"):
                raise SystemExit("[analysis_flow] traces 数组每项须含 component_id")
            _record_trace(manifest, str(item.get("component_id")),
                          decision=str(item.get("decision") or "") or None,
                          missing=item.get("missing"),
                          tool=str(item.get("tool") or "") or None,
                          summary=str(item.get("summary") or ""))
            emitted.append(str(item.get("component_id")))
        _save(manifest)
        _emit({"ok": True, "components": emitted})
        return 0

    if not args.decision and not args.tool:
        raise SystemExit("[analysis_flow] trace 需要 --traces-json（批量）或 --decision/--tool（单个）")
    _record_trace(manifest, args.component_id or "main",
                  decision=args.decision, tool=args.tool,
                  missing=_read_json_arg(args.missing_file, args.missing_json),
                  summary=args.summary or "")
    _save(manifest)
    _emit({"ok": True, "component_id": args.component_id or "main"})
    return 0


def _record_trace(manifest: dict, component_id: str, *, decision: str | None,
                  missing=None, tool: str | None = None, summary: str = "") -> None:
    """单条 trace 记账（sufficiency_trace / explore_rounds + 预算）。"""
    component = _component(manifest, component_id)
    entry = {"at": _now()}
    if decision:
        entry["decision"] = decision
        entry["missing"] = missing or []
        component["sufficiency_trace"].append(entry)
        # compile_uncovered 是服务端编译信号而非模型判断，记 deterministic；
        # 其余 decision 是 LLM 充分性判断。与 sufficiency_trace 同点追加，
        # 保证追踪页按下标逐条对齐。
        _record_llm_step(manifest, "trace",
                         "deterministic" if decision == "compile_uncovered" else "decision",
                         component_id=component_id,
                         ai_output={"decision": decision, "missing": missing or []},
                         context_digest=summary or None,
                         context_payload={"status": component["status"],
                                          "attempts": component["attempts"][-3:]})
        if decision in ("clarify", "infeasible"):
            if manifest.get("mode") == "open" and not _planned(manifest, component_id)["required"]:
                _skip(manifest, component_id, f"optional_{decision}")
            else:
                component["status"] = decision
    elif tool:
        if not _work_allowed(manifest, component_id, explore=True):
            return
        rounds = len(component["explore_rounds"])
        limit = _explore_round_limit(manifest, component)
        if rounds >= limit:
            raise _budget_error(manifest, "explore_rounds", component_id,
                                limit, rounds)
        entry["tool"] = tool
        entry["summary"] = summary
        component["explore_rounds"].append(entry)
        manifest["budget"]["explore_count"] += 1
        _record_llm_step(manifest, "trace", "decision", component_id=component_id,
                         ai_output={"tool": tool, "summary": summary},
                         context_digest=summary or None)


def _compile_one(manifest: dict, analysis_id: str, component_id: str,
                 source_id: str = "", limit: int | None = None) -> tuple[int, dict]:
    """单成分「编译→执行→记账」核心（cmd_compile / cmd_compile_batch 共用）。

    Returns:
        (exit_code, result)——路由信号（not covered）不是错误，exit 0。
    """
    plan_comp = next(
        (c for c in (manifest.get("plan") or {}).get("components") or []
         if str(c.get("component_id") or "") == component_id), None)
    if plan_comp is None:
        raise SystemExit(f"[analysis_flow] 成分 {component_id} 不在计划内——先 plan")
    if not (plan_comp.get("selected_metrics") or []):
        raise SystemExit(f"[analysis_flow] 成分 {component_id} 无选定指标——直接走探索/生成链路")
    sql_cap = ((manifest.get("plan") or {}).get("budget") or {}).get("max_sql", MAX_SQL_COUNT)
    if manifest["budget"]["sql_count"] >= sql_cap:
        raise _budget_error(manifest, "sql_count", "global",
                            sql_cap, manifest["budget"]["sql_count"])
    result = _dc._analysis("analysis", "compile",
                       {"analysis_id": analysis_id, "component": plan_comp})
    if not result.get("covered"):
        _record_trace(manifest, component_id, decision="compile_uncovered",
                      missing=[{"kind": "compile", "what": result.get("reason")}],
                      summary=str(result.get("detail") or ""))
        return 0, result

    _record_llm_step(manifest, "compile", "deterministic", component_id=component_id,
                     context_digest=f"G1 确定性编译+执行（零 LLM）· query_id={result.get('query_id')}",
                     refs=[f"manifest.components.{component_id}.attempts"])

    # covered → 统一执行（编译产物通道：模板+params+query_id，服务端验证重导出）
    body = {
        "analysis_id": analysis_id, "component_id": component_id,
        "compile_query_id": result.get("query_id"),
        "sql_template": result.get("sql_template"),
        "params": result.get("params") or [],
        "db_ref": result.get("db_ref") or "",
        "selected": result.get("selected") or [],
    }
    if source_id:
        body["source_id"] = source_id
    elif manifest["plan"].get("source_id"):
        body["source_id"] = manifest["plan"]["source_id"]
    if limit:
        body["limit"] = limit
    if manifest.get("mode") == "open":
        manifest["budget"]["sql_count"] += 1
        _save(manifest)
    exec_result = _dc._analysis("analysis", "execute", body)
    sql = str(result.get("sql") or "")
    # 「执行→记账→evidence」统一管线（Phase 2b）；编译通道 attempt 多记
    # query_id/release_id，authoritative 等由管线内 _record_evidence 回填
    record_execution(manifest, analysis_id=analysis_id, component_id=component_id,
                     sql=sql, result=exec_result, kind="compile",
                     source_id=source_id, time_window=plan_comp.get("time"),
                     record_extra={"query_id": result.get("query_id"),
                                   "release_id": result.get("release_id")})
    return (0 if exec_result.get("ok") else EXIT_SERVICE), exec_result


def cmd_compile(args: argparse.Namespace) -> int:
    """G1 编译通道：有选定指标的成分先试确定性编译（零 LLM）。

    covered → 编译+执行+记账一步完成（attempt kind="compile"，权威标注，
    免 check）；not covered → 输出 action/reason 路由信号（fix_plan /
    probe_first）记入 sufficiency_trace，交模型决定下一步（路由信号非错误，
    exit 0）。多成分请用 compile-batch（一轮提交，省逐成分往返）。
    """
    manifest = _load(args.analysis_id)
    if not _work_allowed(manifest, args.component_id, sql=True):
        _save(manifest)
        _emit({"ok": True, "skipped": True, "component_id": args.component_id})
        return 0
    if (manifest.get("mode") == "open" and args.source_id and manifest["plan"].get("source_id")
            and args.source_id != manifest["plan"].get("source_id")):
        raise SystemExit("[analysis_flow] source_id 与计划不一致")
    code, result = _compile_one(manifest, args.analysis_id, args.component_id,
                                source_id=args.source_id, limit=args.limit)
    _save(manifest)
    _emit(result, manifest, "compile")
    return code


def cmd_compile_batch(args: argparse.Namespace) -> int:
    """批量 G1 编译：有选定指标的成分一轮全部提交（对齐 check-batch 模式）。

    批量项：[{component_id, source_id?, limit?}]。covered 的编译+执行+记账一步
    完成；not covered 返回路由信号。单项前置错误不炸整批，逐项聚合返回。
    """
    manifest = _load(args.analysis_id)
    batch = _read_json_arg(args.batch_file, args.batch_json)
    if not isinstance(batch, list) or not batch:
        raise SystemExit("[analysis_flow] --batch-json/--batch-file 必须是非空数组")
    # 预算预检（整批拒绝——让模型先收敛再提交）
    sql_cap = ((manifest.get("plan") or {}).get("budget") or {}).get("max_sql", MAX_SQL_COUNT)
    if manifest["budget"]["sql_count"] + len(batch) > sql_cap:
        raise _budget_error(manifest, "sql_count", "global", sql_cap,
                            manifest["budget"]["sql_count"])
    results = []
    for item in batch:
        cid = str(item.get("component_id") or "")
        if not _work_allowed(manifest, cid, sql=True):
            results.append({"ok": True, "skipped": True, "component_id": cid})
            continue
        try:
            _code, result = _compile_one(manifest, args.analysis_id, cid,
                                         source_id=str(item.get("source_id") or ""),
                                         limit=item.get("limit"))
            result["component_id"] = cid
            results.append(result)
        except SystemExit as exc:  # 单项前置错误（不在计划内/无指标）不炸整批
            results.append({"ok": False, "component_id": cid,
                            "error": str(exc).replace("[analysis_flow] ", "")})
    _save(manifest)
    ok = all(r.get("ok") or r.get("covered") is False for r in results)
    _emit({"ok": ok, "results": results}, manifest, "compile-batch")
    return 0 if ok else EXIT_SERVICE


def cmd_check(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    component = _component(manifest, args.component_id)
    sql = _read_text_arg(args.sql_file, args.sql)
    body = {"sql": sql}
    selected = _read_json_arg(args.selected_file, args.selected_json)
    if selected:
        body["selected"] = selected
    temp_scope = _read_json_arg(args.temp_scope_file, args.temp_scope_json)
    if temp_scope:
        body["temp_scope"] = temp_scope
    if args.time_json:
        body["time"] = json.loads(args.time_json)
    result = _dc._analysis("analysis", "check", body)
    result["sql_hash"] = _sql_hash(sql)
    component["attempts"].append({
        "kind": "check", "sql_hash": result["sql_hash"], "sql": sql, "at": _now(),
        "checks": result.get("checks"),
        "grounding_level": result.get("grounding_level"),
        "retry_hint": result.get("retry_hint"),
    })
    checks = result.get("checks") or []
    _record_llm_step(manifest, "check", "deterministic", component_id=args.component_id,
                     context_digest=(f"{sum(1 for c in checks if c.get('status') == 'pass')}"
                                     f"/{len(checks)} 项通过 · retry_hint: {result.get('retry_hint') or '—'}"),
                     refs=[f"manifest.components.{args.component_id}.attempts"])
    failed = any(c.get("status") == "fail" for c in result.get("checks") or ())
    if failed:
        component["retries"] += 1
        if component["retries"] > MAX_RETRIES:
            _save(manifest)
            raise _budget_error(manifest, "retries", args.component_id,
                                MAX_RETRIES, component["retries"] - 1)
        component["status"] = "retrying"
    _save(manifest)
    _emit(result)
    return EXIT_USAGE if failed else 0


def cmd_execute(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    # d5 软提示：剩余多成分仍逐个执行 = 批处理纪律未遵守（实测达标 8 次 vs
    # 不达标 21 次决策）——hint 进 result JSON（stdout 直印会破坏输出契约）
    _plan_ids = [c.get("id") if isinstance(c, dict) else str(c)
                 for c in (manifest.get("plan", {}).get("components") or [])]
    _states = manifest.get("components") or {}
    _pending = [cid for cid in _plan_ids
                if (_states.get(cid) or {}).get("status") not in
                ("done", "skipped", "failed", "retries_exhausted")]
    batch_hint = (f"[hint] 待执行成分还有 {len(_pending)} 个——多成分请改用 batch 命令"
                  "（线程池并行，单轮完成）" if len(_pending) >= 2 else "")
    if not _work_allowed(manifest, args.component_id, sql=True):
        _save(manifest)
        _emit({"ok": True, "skipped": True, "component_id": args.component_id})
        return 0
    component = _component(manifest, args.component_id)
    if manifest["budget"]["sql_count"] >= MAX_SQL_COUNT:
        raise _budget_error(manifest, "sql_count", "global",
                            MAX_SQL_COUNT, manifest["budget"]["sql_count"])
    sql = _read_text_arg(args.sql_file, args.sql)
    body = {"analysis_id": args.analysis_id, "component_id": args.component_id,
            "sql": sql}
    if args.source_id:
        body["source_id"] = args.source_id
    selected = _read_json_arg(args.selected_file, args.selected_json)
    if selected:
        body["selected"] = selected
    temp_scope = _read_json_arg(args.temp_scope_file, args.temp_scope_json)
    if temp_scope:
        body["temp_scope"] = temp_scope
    if args.limit:
        body["limit"] = args.limit
    if args.time_json:
        body["time"] = json.loads(args.time_json)
    if not _check_before_execute(manifest, args.component_id, body):
        _save(manifest)
        _emit({"ok": False, "error": "sql_check_failed", "component_id": args.component_id})
        return EXIT_USAGE
    # 记在检查通过之后：保证 llm_trace[execute] 与 attempts[kind=execute] 逐条对齐
    # （检查失败的早退路径不产生 execute attempt，也不产生本条目）。
    _record_llm_step(manifest, "execute", "generation", component_id=args.component_id,
                     ai_output=sql,
                     context_digest="基于 L2 上下文包（schema_tables/维值候选）与检查结果生成 SQL（G2/G3 路径）",
                     context_payload=_llm_execute_context(manifest, component, args.component_id),
                     refs=[f"manifest.components.{args.component_id}.attempts"])
    if manifest.get("mode") == "open":
        manifest["budget"]["sql_count"] += 1
        _save(manifest)
    result = _dc._analysis("analysis", "execute", body)
    # 「执行→记账→evidence」统一管线（Phase 2b）
    record_execution(manifest, analysis_id=args.analysis_id, component_id=args.component_id,
                     sql=sql, result=result, kind="execute",
                     source_id=args.source_id, time_window=body.get("time"))
    _save(manifest)
    if batch_hint:
        result["hint"] = batch_hint
    _emit(result, manifest, "execute")
    return 0 if result.get("ok") else EXIT_SERVICE


def cmd_probe(args: argparse.Namespace) -> int:
    """维值探查。``--targets "t1.c1,t2.c2"`` 批量确认，**整批算一轮探索**。

    短路路径的过滤值全是 pending（该路不跑维值路），逐列 probe 会一列一轮
    把探索预算烧光——批量形态让「一轮确认全部待定值」成为可执行指令。
    """
    manifest = _load(args.analysis_id)
    if not _explore_allowed(manifest, args.component_id):
        return 0
    component = _component(manifest, args.component_id)
    rounds = len(component["explore_rounds"])
    limit = _explore_round_limit(manifest, component)
    if rounds >= limit:
        raise _budget_error(manifest, "explore_rounds", args.component_id,
                            limit, rounds)
    targets: list[tuple[str, str]] = []
    if args.targets:
        targets = [_split_qualified(item) for item in str(args.targets).split(",") if item.strip()]
    elif args.table and args.column:
        targets = [(args.table, args.column)]
    if not targets:
        raise SystemExit("[analysis_flow] probe 需要 --targets 或 --table/--column")

    probes: list[dict] = []
    exit_code = 0
    for table, column in targets:
        body = {"source_id": args.source_id, "table": table, "column": column}
        if args.like:
            body["like"] = args.like
        if args.limit:
            body["limit"] = args.limit
        result = _dc._analysis("analysis", "probe", body)
        probes.append({"table": table, "column": column, **result})
        if result.get("error"):
            exit_code = EXIT_SERVICE
    component["explore_rounds"].append({
        "tool": "probe_values",
        "targets": [f"{t}.{c}" for t, c in targets],
        "like": args.like or "",
        "values": {f"{p['table']}.{p['column']}": (p.get("values") or [])[:10] for p in probes},
        "at": _now()})
    _record_llm_step(manifest, "probe", "decision", component_id=args.component_id,
                     ai_output=[f"{t}.{c}" for t, c in targets],
                     context_digest="维值确认目标由模型选择（过滤值 pending 等）")
    manifest["budget"]["explore_count"] += 1
    _save(manifest)
    _emit(probes[0] if len(probes) == 1 else {"probes": probes}, manifest, "probe")
    return exit_code


def cmd_join(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    if not _explore_allowed(manifest, args.component_id):
        return 0
    component = _component(manifest, args.component_id)
    rounds = len(component["explore_rounds"])
    limit = _explore_round_limit(manifest, component)
    if rounds >= limit:
        raise _budget_error(manifest, "explore_rounds", args.component_id,
                            limit, rounds)
    left_table, left_column = _split_qualified(args.left)
    right_table, right_column = _split_qualified(args.right)
    body = {"source_id": args.source_id,
            "left": {"table": left_table, "column": left_column},
            "right": {"table": right_table, "column": right_column},
            "sample": bool(args.sample)}
    result = _dc._analysis("analysis", "check-join", body)
    component["explore_rounds"].append({
        "tool": "check_join", "left": args.left, "right": args.right,
        "type_match": result.get("type_match"),
        "inclusion_ratio": result.get("inclusion_ratio"), "at": _now()})
    _record_llm_step(manifest, "join", "decision", component_id=args.component_id,
                     ai_output={"left": args.left, "right": args.right},
                     context_digest="join 证据确认目标由模型选择")
    manifest["budget"]["explore_count"] += 1
    _save(manifest)
    _emit(result, manifest, "join")
    return 0


def _require_full_db_ref(db_ref: str) -> None:
    """db-ref 必须是自带库名的完整指针——裸库名会被 dwcli 以「缺少路径变量
    --db」拒绝，且 exit 2 时报错 JSON 不透出（实测空 stdout，agent 拿不到
    修正线索，白烧一轮）。
    """
    if db_ref and "://" not in db_ref:
        raise SystemExit(
            f"[analysis_flow] --db-ref 须为完整指针（datasource current 的返回值，"
            f"如 mysql://127.0.0.1:33071/biz_eval），裸库名 {db_ref!r} 无法定位库"
            "——或直接省略 --db-ref，使用工作空间默认业务库")


def cmd_list_tables(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    if not _explore_allowed(manifest, args.component_id):
        return 0
    component = _component(manifest, args.component_id)
    rounds = len(component["explore_rounds"])
    limit = _explore_round_limit(manifest, component)
    if rounds >= limit:
        raise _budget_error(manifest, "explore_rounds", args.component_id,
                            limit, rounds)
    _require_full_db_ref(args.db_ref)
    cli_args = ["databases", "tables"]
    if args.db_ref:
        cli_args += ["--db-ref", args.db_ref]
    result = _dc.dwcli(cli_args)
    component["explore_rounds"].append({"tool": "list_tables", "at": _now()})
    _record_llm_step(manifest, "list-tables", "decision",
                     component_id=args.component_id or "main",
                     context_digest="活库表清单探索由模型选择")
    manifest["budget"]["explore_count"] += 1
    _save(manifest)
    _emit(result, manifest, "list-tables")
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    if not _explore_allowed(manifest, args.component_id):
        return 0
    component = _component(manifest, args.component_id)
    rounds = len(component["explore_rounds"])
    limit = _explore_round_limit(manifest, component)
    if rounds >= limit:
        raise _budget_error(manifest, "explore_rounds", args.component_id,
                            limit, rounds)
    _require_full_db_ref(args.db_ref)
    cli_args = ["databases", "table", "--table", args.table]
    if args.db_ref:
        cli_args += ["--db-ref", args.db_ref]
    result = _dc.dwcli(cli_args)
    component["explore_rounds"].append(
        {"tool": "describe_table", "table": args.table, "at": _now()})
    _record_llm_step(manifest, "describe", "decision",
                     component_id=args.component_id or "main",
                     ai_output=args.table, context_digest="表结构探索由模型选择")
    manifest["budget"]["explore_count"] += 1
    _save(manifest)
    _emit(result, manifest, "describe")
    return 0


def cmd_check_batch(args: argparse.Namespace) -> int:
    """批量程序化检查：无依赖成分的 SQL 一轮全部提交（并行度 2）。

    批量项：[{component_id, sql, selected?, temp_scope?, time?}]——与单个
    check 同字段。重试预算按成分独立硬执行。
    """
    manifest = _load(args.analysis_id)
    batch = _read_json_arg(args.batch_file, args.batch_json)
    if not isinstance(batch, list) or not batch:
        raise SystemExit("[analysis_flow] --batch-json/--batch-file 必须是非空数组")
    # 预算预检（逐成分；有超限的整批拒绝——让模型先收敛再提交）
    for item in batch:
        component_id = str(item.get("component_id") or "")
        component = _component(manifest, component_id)
        if (component.get("retries") or 0) >= MAX_RETRIES + 1:
            raise _budget_error(manifest, "retries", component_id,
                                MAX_RETRIES, component.get("retries") or 0)
    bodies = []
    for item in batch:
        body = {"sql": str(item.get("sql") or "")}
        if item.get("selected"):
            body["selected"] = item["selected"]
        if item.get("temp_scope"):
            body["temp_scope"] = item["temp_scope"]
        if item.get("time"):
            body["time"] = item["time"]
        bodies.append(body)
    responses = _run_batch(bodies, "check")
    summary = []
    for index, item in enumerate(batch):
        component_id = str(item.get("component_id") or f"c{index}")
        component = _component(manifest, component_id)
        result = responses.get(index, {"ok": False, "error": "missing"})
        sql = str(item.get("sql") or "")
        result["sql_hash"] = _sql_hash(sql)
        component["attempts"].append({
            "kind": "check", "sql_hash": result["sql_hash"], "sql": sql,
            "at": _now(), "checks": result.get("checks"),
            "grounding_level": result.get("grounding_level"),
            "retry_hint": result.get("retry_hint"),
        })
        checks = result.get("checks") or []
        _record_llm_step(manifest, "check", "deterministic", component_id=component_id,
                         context_digest=(f"{sum(1 for c in checks if c.get('status') == 'pass')}"
                                         f"/{len(checks)} 项通过 · retry_hint: {result.get('retry_hint') or '—'}"),
                         refs=[f"manifest.components.{component_id}.attempts"])
        failed = any(c.get("status") == "fail" for c in result.get("checks") or ())
        if failed:
            component["retries"] = (component.get("retries") or 0) + 1
            component["status"] = "retrying"
        summary.append({"component_id": component_id, "failed": failed,
                        "checks": result.get("checks"),
                        "retry_hint": result.get("retry_hint")})
    _save(manifest)
    _emit({"ok": not any(s["failed"] for s in summary), "results": summary}, manifest, "check-batch")
    return EXIT_USAGE if any(s["failed"] for s in summary) else 0


def cmd_execute_batch(args: argparse.Namespace) -> int:
    """批量统一执行：检查通过的成分并行执行（并行度 2）+ 逐项结果检查。"""
    manifest = _load(args.analysis_id)
    batch = _read_json_arg(args.batch_file, args.batch_json)
    if not isinstance(batch, list) or not batch:
        raise SystemExit("[analysis_flow] --batch-json/--batch-file 必须是非空数组")
    if manifest.get("mode") == "open":
        # 每波仅运行无依赖的同优先级层；下一波由 next 返回。
        ids = [item.get("component_id") for item in batch]
        if len(ids) != len(set(ids)):
            raise SystemExit("[analysis_flow] batch component_id 重复")
        batch = sorted(batch, key=lambda item: -_planned(manifest, item["component_id"])["priority"])
        batch = [item for item in batch if _work_allowed(manifest, item["component_id"], sql=True)]
        capacity = ((manifest["plan"].get("budget") or {}).get("max_sql", MAX_SQL_COUNT)
                    - manifest["budget"].get("sql_count", 0))
        if len(batch) > capacity:
            if any(_planned(manifest, item["component_id"])["required"] for item in batch[capacity:]):
                raise SystemExit("[analysis_flow] required 批次超过剩余 SQL 预算，请缩小批次")
            for item in batch[capacity:]:
                _skip(manifest, item["component_id"], "sql_budget_exhausted")
            batch = batch[:capacity]
        _save(manifest)
        if not batch:
            _emit({"ok": True, "results": [], "skipped": True})
            return 0
    budget = manifest.setdefault("budget", {})
    if budget.get("sql_count", 0) + len(batch) > MAX_SQL_COUNT:
        raise _budget_error(manifest, "sql_count", "global",
                            MAX_SQL_COUNT, budget.get("sql_count", 0))
    bodies = []
    for item in batch:
        body = {"analysis_id": args.analysis_id,
                "component_id": str(item.get("component_id") or ""),
                "sql": str(item.get("sql") or "")}
        for key in ("source_id", "selected", "temp_scope", "time"):
            if item.get(key):
                body[key] = item[key]
        if item.get("limit"):
            body["limit"] = item["limit"]
        bodies.append(body)
    if manifest.get("mode") == "open":
        checked = [_check_before_execute(manifest, body["component_id"], body) for body in bodies]
        if not all(checked):
            _save(manifest)
            _emit({"ok": False, "error": "sql_check_failed", "checked": checked})
            return EXIT_USAGE
    if manifest.get("mode") == "open":
        budget["sql_count"] = budget.get("sql_count", 0) + len(bodies)
        _save(manifest)
    parallelism = ((manifest.get("plan") or {}).get("budget") or {}).get("parallelism", BATCH_PARALLELISM)
    responses = _run_batch(bodies, "execute", parallelism)
    summary = []
    for index, item in enumerate(batch):
        component_id = str(item.get("component_id") or f"c{index}")
        component = _component(manifest, component_id)
        result = responses.get(index, {"ok": False, "error": "missing"})
        sql = str(item.get("sql") or "")
        _record_llm_step(manifest, "execute", "generation", component_id=component_id,
                         ai_output=sql,
                         context_digest="基于 L2 上下文包（schema_tables/维值候选）与检查结果生成 SQL（G2/G3 路径）",
                         context_payload=_llm_execute_context(manifest, component, component_id),
                         refs=[f"manifest.components.{component_id}.attempts"])
        # 「执行→记账→evidence」统一管线（Phase 2b）
        record_execution(manifest, analysis_id=args.analysis_id, component_id=component_id,
                         sql=sql, result=result, kind="execute",
                         source_id=item.get("source_id", ""), time_window=item.get("time"))
        summary.append({"component_id": component_id, "ok": bool(result.get("ok")),
                        "csv_path": result.get("csv_path"),
                        "evidence_id": result.get("evidence_id"),
                        "grounding_level": result.get("grounding_level"),
                        "authoritative": bool(result.get("authoritative")),
                        "result_checks": result.get("result_checks"),
                        "row_count": result.get("row_count")})
    _save(manifest)
    payload = {"ok": all(s["ok"] for s in summary), "results": summary}
    exec_hints = _execute_hints(summary)
    if exec_hints:
        payload["hints"] = exec_hints
    _emit(payload, manifest, "execute-batch")
    return 0 if all(s["ok"] for s in summary) else EXIT_SERVICE


def cmd_result_check(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    payload = _read_json_arg(args.input_file, args.input_json)
    result = _dc._analysis("analysis", "result-check", payload)
    manifest.setdefault("result_checks", {})[payload.get("component_id") or ""] = result.get("checks")
    _save(manifest)
    _emit(result, manifest, "result-check")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    _emit(manifest, manifest, "manifest")
    return 0


def _check_before_execute(manifest: dict, component_id: str, body: dict) -> bool:
    """开放模式在执行前重验同一计划的 selected/time，避免漏传绕开检查。"""
    if manifest.get("mode") != "open":
        return True
    planned = _planned(manifest, component_id)
    expected_selected = [{"metric_key": key} for key in planned.get("selected_metrics") or []]
    for key, expected in (("selected", expected_selected), ("time", planned.get("time"))):
        if key in body and body[key] != expected:
            raise SystemExit(f"[analysis_flow] {component_id} 的 {key} 与计划不一致，请先修计划")
        body[key] = expected
    source_id = str((manifest.get("plan") or {}).get("source_id") or "")
    if source_id:
        if body.get("source_id") and body["source_id"] != source_id:
            raise SystemExit("[analysis_flow] source_id 与计划不一致")
        body["source_id"] = source_id
    component = _component(manifest, component_id)
    if component["retries"] > MAX_RETRIES:
        raise _budget_error(manifest, "retries", component_id, MAX_RETRIES, component["retries"])
    outcome = _dc._analysis("analysis", "check", body)
    checks = outcome.get("checks") or []
    failed = not checks or any(check.get("status") == "fail" for check in checks)
    component["attempts"].append({
        "kind": "check", "sql_hash": _sql_hash(body["sql"]), "sql": body["sql"],
        "at": _now(), "checks": checks, "retry_hint": outcome.get("retry_hint"),
    })
    if failed:
        component["retries"] += 1
        component["status"] = "retrying" if component["retries"] <= MAX_RETRIES else "failed"
    return not failed


def _explore_allowed(manifest: dict, component_id: str) -> bool:
    if _work_allowed(manifest, component_id, explore=True):
        return True
    _save(manifest)
    _emit({"ok": True, "skipped": True, "component_id": component_id,
           "reason": _component(manifest, component_id).get("skip_reason")})
    return False


def cmd_next(args: argparse.Namespace) -> int:
    """返回当前可执行波次；预算耗尽的 optional 自动跳过并写账本。"""
    manifest = _load(args.analysis_id)
    components = (manifest.get("plan") or {}).get("components") or []
    terminal = {"done", "failed", "skipped", "clarify", "infeasible"}
    pending = [c for c in components if _component(manifest, c["component_id"])["status"] not in terminal]
    required = [c for c in pending if c.get("required", True)]
    ready = []
    for planned in sorted(required or pending, key=lambda c: -c.get("priority", 100)):
        deps = [_component(manifest, dep)["status"] for dep in planned.get("depends_on") or []]
        if not planned.get("required", True) and any(status in terminal - {"done"} for status in deps):
            _skip(manifest, planned["component_id"], "dependency_unavailable")
            continue
        if all(_component(manifest, dep)["status"] == "done" for dep in planned.get("depends_on") or []):
            if _work_allowed(manifest, planned["component_id"], sql=True, advisory=True):
                ready.append(planned)
    _save(manifest)
    parallelism = ((manifest.get("plan") or {}).get("budget") or {}).get("parallelism", 2)
    _emit({"components": ready[:parallelism], "uncovered": manifest.get("uncovered") or [],
           "evidence": manifest.get("evidence") or [], "manifest_path": str(_mm._manifest_path(args.analysis_id))})
    return 0


def cmd_skip(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    if _planned(manifest, args.component_id).get("required", True):
        raise SystemExit(
            "[analysis_flow] required 成分不能静默跳过。按实际情况三选一：\n"
            "  ① 成分确实无法取数 → trace --component-id <id> --decision infeasible "
            "--summary <原因>（注意：infeasible 后该成分按无证据处理，required 成分的"
            "证据断言会判失败——若用户/用例期望该成分交付，这是设计行为）\n"
            "  ② 已产出替代粒度/替代口径的合格证据（如季度不可编译但月度序列已 G1 落账）"
            " → 保持 done，不要标 infeasible——evidence 已在账上即算覆盖，粒度替代写进 "
            "summary/notes 说明\n"
            "  ③ 需要用户澄清 → trace --component-id <id> --decision clarify --summary <分歧点>\n"
            "  （full-verify-1029 ck-27 实证：季度粒度不可编译、月度证据已落账，误标 "
            "infeasible 把合格证据自我埋葬，核心证据断言失败）")
    _skip(manifest, args.component_id, args.reason)
    _save(manifest)
    _emit({"ok": True, "component_id": args.component_id, "status": "skipped", "reason": args.reason})
    return 0


def cmd_report_gate(args: argparse.Namespace) -> int:
    manifest = _load(args.analysis_id)
    spec = _read_json_arg(args.spec_file, None)
    if not isinstance(spec, dict):
        raise SystemExit("[analysis_flow] 报告 spec 必须是对象")
    result = report_gate(spec, manifest)
    # gate 失败也记账——spec 已由模型产出（重试轮依据）
    _record_llm_step(manifest, "report-gate", "generation", ai_output=spec,
                     context_digest=f"开放报告 spec 由模型撰写 · 可用证据 {len(manifest.get('evidence') or [])} 条",
                     context_payload={"evidence": [
                         {"evidence_id": e.get("evidence_id"), "component_id": e.get("component_id"),
                          "artifact": e.get("artifact"), "grounding_level": e.get("grounding_level"),
                          "authoritative": e.get("authoritative"),
                          "quality": (e.get("quality") or {}).get("status")}
                         for e in manifest.get("evidence") or []]})
    manifest["report_gate"] = result
    _save(manifest)
    gate_hints = _report_gate_hints(result)
    if gate_hints:
        result["hints"] = gate_hints
    _emit(result, manifest, "report-gate")
    return 0 if result["ok"] else EXIT_USAGE


def _read_json_arg(file_arg: str | None, json_arg: str | None) -> dict | list | None:
    """读 JSON 参数：优先文件，其次内联串，``-`` 表示 stdin。

    内联 JSON 在 Windows shell 下易被引号处理破坏（单引号非定界符、中文转义），
    故非法 JSON 给**友好报错**（SystemExit 而非 Traceback），并提示改用文件/stdin。
    """
    def _parse(text: str, source: str) -> dict | list:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"[analysis_flow] {source} 不是合法 JSON: {exc}。"
                "Windows shell 会破坏单引号/中文 JSON——改用 `--*-file <文件路径>`，"
                "或写文件后管道：`... --*-json -`（从 stdin 读）") from None

    if file_arg == "-" or json_arg == "-":
        return _parse(sys.stdin.read(), "stdin")
    if file_arg:
        try:
            text = Path(file_arg).read_text("utf-8")
        except OSError as exc:
            raise SystemExit(f"[analysis_flow] 读不到 JSON 文件 {file_arg}: {exc}") from None
        return _parse(text, file_arg)
    if json_arg:
        # 容错：--*-json 收到的值若不像内联 JSON（对象/数组不以 { [ 开头）而是
        # 现存文件路径，直接按文件读——把文件路径误传给 --*-json 是高频误猜
        # （funcverify-1029 实测 olist-02/06 两轮、上轮 ck-24 同型，每轮烧一次
        # 自愈）。文件读不出来再走原有的友好报错。
        if json_arg.lstrip()[:1] not in ("{", "[", '"'):
            candidate = Path(json_arg)
            if candidate.is_file():
                return _parse(candidate.read_text("utf-8"), json_arg)
        return _parse(json_arg, "--*-json 内联串")
    return None


def _read_text_arg(file_arg: str | None, text_arg: str | None) -> str:
    if file_arg:
        return Path(file_arg).read_text("utf-8").strip()
    if text_arg:
        return text_arg.strip()
    raise SystemExit("[analysis_flow] 需要 --sql-file 或 --sql 之一")


def _split_qualified(qualified: str) -> tuple[str, str]:
    parts = str(qualified).rsplit(".", 1)
    if len(parts) != 2 or not all(parts):
        raise SystemExit(f"[analysis_flow] 需要「表.列」形态，收到 {qualified!r}")
    return parts[0], parts[1]
