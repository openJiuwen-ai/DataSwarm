#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""observability.py —— hints 注入 + llm_trace/stdout_trace 采集（Phase 2c 迁入）。

LLM 参与步骤采集（ANALYSIS_LLM_TRACE 门控，默认关）与 stdout 体量打点
（stdout_trace 恒开启）是「哪条命令在喂胖 LLM 上下文」的观测面；hints
是给模型的按状态行为提示（歧义消解/行数预判/空结果回退/交付纪律）。
"""
from __future__ import annotations

import json
import os
from datetime import date

from .manifest import _now, _save


# LLM 参与步骤采集（默认关）：每条命令是独立子进程，env 即时生效；关闭时
# manifest 不出现 llm_trace 字段（零开销，旧会话零影响）。
LLM_TRACE = os.environ.get("ANALYSIS_LLM_TRACE", "").strip().lower() in {"1", "true", "on"}


LLM_TRACE_OUTPUT_MAX = 4000


LLM_TRACE_DIGEST_MAX = 600


LLM_TRACE_CONTEXT_MAX = int(os.environ.get("ANALYSIS_LLM_TRACE_CONTEXT_MAX", "50000"))


_ROW_ESTIMATE_LIMIT = 500


def _record_llm_step(manifest: dict, step: str, llm_role: str, *,
                     component_id: str | None = None, ai_output: object = None,
                     context_digest: str | None = None,
                     context_payload: object = None,
                     refs: list[str] | None = None) -> None:
    """ANALYSIS_LLM_TRACE=1 时向 manifest["llm_trace"] 追加一条 LLM 参与步骤标注。

    llm_role 三档：deterministic=服务端确定性（零 LLM）/ decision=LLM 决策 /
    generation=LLM 生成。ai_output 为该步 LLM 产出（dict/list 序列化为 JSON），
    超 LLM_TRACE_OUTPUT_MAX 截断并置 ai_output_truncated；context_digest 为
    一行摘要（折叠视图用）；context_payload 为**该步填充给 LLM 的实际输入材料**
    （上一命令的工具结果/成分状态——不含系统提示词与 skill 指引），超
    LLM_TRACE_CONTEXT_MAX 截断并置 context_truncated。refs 指路 manifest 已有
    大段内容。关闭时 no-op；本函数不触发 _save，持久化由各子命令既有 _save
    承担（调用点必须在 _save 之前）。
    """
    if not LLM_TRACE:
        return
    if ai_output is not None and not isinstance(ai_output, str):
        try:
            ai_output = json.dumps(ai_output, ensure_ascii=False)
        except TypeError:
            ai_output = str(ai_output)
    out_chars: int | None = None
    if ai_output is not None:
        out_chars = len(ai_output)
    truncated = False
    if ai_output is not None and len(ai_output) > LLM_TRACE_OUTPUT_MAX:
        ai_output, truncated = ai_output[:LLM_TRACE_OUTPUT_MAX], True
    payload_text: str | None = None
    payload_truncated = False
    in_chars: int | None = None
    if context_payload is not None:
        payload_text = context_payload if isinstance(context_payload, str) else (
            json.dumps(context_payload, ensure_ascii=False))
        in_chars = len(payload_text)
        if len(payload_text) > LLM_TRACE_CONTEXT_MAX:
            payload_text, payload_truncated = payload_text[:LLM_TRACE_CONTEXT_MAX], True
    if context_digest and len(context_digest) > LLM_TRACE_DIGEST_MAX:
        context_digest = context_digest[:LLM_TRACE_DIGEST_MAX] + "…"
    entry: dict = {"step": step, "llm_role": llm_role, "at": _now()}
    if component_id:
        entry["component_id"] = component_id
    if context_digest:
        entry["context_digest"] = context_digest
    if payload_text is not None:
        entry["context_payload"] = payload_text
        entry["context_truncated"] = payload_truncated
    # in/out_chars 为截断前原始长度：体量观测不因采集上限失真（方案
    # docs/proposals/llm-context-slimming-token-accounting-plan.md §2）
    if in_chars is not None:
        entry["in_chars"] = in_chars
    if out_chars is not None:
        entry["out_chars"] = out_chars
    if ai_output is not None:
        entry["ai_output"] = ai_output
        entry["ai_output_truncated"] = truncated
    if refs:
        entry["refs"] = refs
    manifest.setdefault("llm_trace", []).append(entry)


def _llm_execute_context(manifest: dict, component: dict, component_id: str) -> dict:
    """execute 步填充给 LLM 的最终上下文（写 SQL 的全部依据）。

    计划成分定义 + 表列子包（成分级补召回优先于主 context）+ 最近检查反馈
    （重写依据）+ 该成分适用规则/样例（计划校验响应带回，manifest.knowledge）。
    """
    packs = ((component.get("context") or {}).get("packs")
             or (manifest.get("context") or {}).get("packs") or {})
    last_check = next((a for a in reversed(component["attempts"]) if a.get("kind") == "check"), None)
    plan_component = next(
        (c for c in (manifest.get("plan") or {}).get("components") or []
         if str(c.get("component_id") or "") == component_id), None)
    context: dict = {"plan_component": plan_component,
                     "schema_tables": packs.get("schema_tables") or [],
                     "latest_check": last_check}
    knowledge = ((manifest.get("knowledge") or {}).get("components") or {}).get(component_id)
    if knowledge:
        # P2：trace 里的 knowledge 只留引用（asset_id+title）——正文在
        # manifest.knowledge.rules/examples 全局区一份；逐成分复制正文曾把
        # manifest 撑到 200K+。旧 manifest（改版前全量形态）就地降级为引用。
        context["knowledge"] = {
            "rules": [{"asset_id": r.get("asset_id"), "title": r.get("title")}
                      for r in knowledge.get("rules") or []],
            "examples": [{"asset_id": e.get("asset_id"), "title": e.get("title")}
                         for e in knowledge.get("examples") or []],
        }
    return context


def _context_hints(manifest: dict) -> list[str]:
    """context 命令的按状态提示：歧义在场 → 消歧纪律。"""
    ambiguities = (manifest.get("context") or {}).get("ambiguities") or []
    if not ambiguities:
        return []
    terms = "、".join(
        str(a.get("term") or a.get("text") or a) for a in ambiguities[:3])
    return [
        f"歧义在场（{terms}）——先按 references/clarification.md 消歧：工具"
        "（recall_more 换问法/describe 看列注释/术语回源）能消的不问用户，消不掉才 "
        "clarify（说明已知/只问一个/给选项/说影响）；禁止因名称更像核心指标而自行选择"
        "（多解不猜——猜错口径的数是精确地错）",
    ]


def _time_grain_units(time_range: dict) -> int:
    """时间粒度折算的分组行数（日=天数、周=周数、月≈月数；无时间按 1）。"""
    start, end = time_range.get("start"), time_range.get("end_exclusive")
    if not start or not end:
        return 1
    try:
        s = date.fromisoformat(str(start)[:10])
        e = date.fromisoformat(str(end)[:10])
    except ValueError:
        return 1
    span = max((e - s).days, 1)
    grain = str(time_range.get("grain") or "day").lower()
    if grain == "week":
        return max(span // 7, 1)
    if grain in ("month", "monthly"):
        return max(span // 30, 1)
    return span


def _plan_row_hints(manifest: dict) -> list[str]:
    """plan 通过后的行数预判（B2，F-047 根治：超截断线先降粒度，不等执行截断回炉）。

    维度基数取 context schema 子包列的 value_candidates 数量——召回候选是
    真实基数的下界，预估偏保守（提示阈值从严，不阻塞执行）。
    """
    packs = (manifest.get("context") or {}).get("packs") or {}
    col_cards: dict[str, int] = {}
    for table in packs.get("schema_tables") or []:
        for column in (table.get("columns") or []):
            n = len(column.get("value_candidates") or [])
            if n:
                for key in (column.get("name"), column.get("node_id")):
                    if key:
                        col_cards[str(key)] = max(col_cards.get(str(key), 0), n)
    hints: list[str] = []
    for planned in (manifest.get("plan") or {}).get("components") or []:
        dims = [str(d) for d in planned.get("dimensions") or []]
        matched = [d for d in dims if d in col_cards]
        if not matched:
            continue
        cardinality = 1
        for d in matched:
            cardinality *= col_cards[d]
        units = _time_grain_units(planned.get("time") or {})
        estimate = cardinality * units
        if estimate > _ROW_ESTIMATE_LIMIT:
            hints.append(
                f"{planned.get('component_id')}：维度基数×时间粒度预估约 {estimate} 行"
                f"（>{_ROW_ESTIMATE_LIMIT} 截断线）——先降粒度（日→周/月）或收窄维度，"
                "不要等执行截断后回炉（实测截断修正多烧 4-5 轮）")
    return hints[:3]


def _execute_hints(summary: list) -> list[str]:
    """execute 命令的按状态提示：空结果回退 / 截断明示。"""
    hints: list[str] = []
    for item in summary:
        cid = item.get("component_id")
        for check in item.get("result_checks") or []:
            if not isinstance(check, dict) or check.get("status") != "fail":
                continue
            name = check.get("name")
            if name == "empty_result":
                hints.append(
                    f"{cid} 结果为空——按空结果回退矩阵排查（scope→歧义→过滤条件，"
                    "见 references/exploration.md）后才能认定「该口径下无数据」；"
                    "「无数据」与「值为 0」是两个不同的业务结论")
            elif name == "truncated":
                hints.append(
                    f"{cid} 结果被截断——下游统计不可作为总量口径，结论必须标注"
                    "「数据可能被截断」；需要精确总量时回取数层收敛（SQL 聚合/降粒度）")
    return hints[:3]


def _report_gate_hints(result: dict) -> list[str]:
    """report-gate 通过后的收口提示。"""
    if not result.get("ok"):
        return []
    return [
        "进入 dw-report 组装报告——正文里由多行数据加总/折算得出的数字，"
        "写完后逐项对照证据 CSV 重算一遍再定稿（誊写错位不会被脚本拦截）；"
        "归因类结论带未解释比例；洞察条目自标 claim_type（fact/interpretation/uncertainty）",
        "交付答复（send_file_to_user 前后）：必须写 2-3 条结论摘要且**关键数值直接"
        "写在答复正文**——对比类给全部对比项数值、趋势类给首末与极值；只写「已完成"
        "分析并出报告，文件已发送」而不带数字是不合格交付（F-011）",
    ]


def _emit(payload: dict, manifest: dict | None = None, step: str = "") -> None:
    """stdout 输出命令结果；带 (manifest, step) 时同步打点 stdout 体量。

    stdout_trace 恒开启（与 ANALYSIS_LLM_TRACE 无关）：一行/命令的成本，回答
    「哪条命令在喂胖 LLM 上下文」——2026-10-21 会话 plan 命令 stdout 128K 尖峰
    即因缺此打点靠事后解剖才发现（方案
    docs/proposals/llm-context-slimming-token-accounting-plan.md §2）。
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if manifest is not None and step:
        manifest.setdefault("stdout_trace", []).append(
            {"step": step, "at": _now(), "stdout_chars": len(text)})
        _save(manifest)
    print(text)
