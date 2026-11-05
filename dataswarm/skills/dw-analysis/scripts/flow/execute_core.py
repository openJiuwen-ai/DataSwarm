#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""execute_core.py —— 单成分「执行→记账→evidence」管线（Phase 2b）。

_compile_one / cmd_execute / cmd_execute_batch 三份近似拷贝的后处理统一
于此（golden 契约测试锁定字段集与顺序语义，见
tests/unit_tests/skills/test_analysis_flow_execute_golden.py）。

成功路径：CSV 落盘 → attempt 记录（kind 维度 + record_extra 附加字段，
如编译通道的 query_id/release_id）→ result_checks → 结果富化 → evidence
登记；失败路径：失败 attempt + status=failed。closed 模式的 sql_count
「成功后计数」在管线内；open 模式的「先加后执行」留在调用方（预执行
时机在 HTTP 调用前，属编排决策）。authoritative / query_semantics_verified
由 _record_evidence 从 result 统一回填，调用方无需在 record_extra 重复。

本模块对 dwcli 的调用经模块属性解析（``_dc._analysis``）——测试打桩
seam 在 flow.dwcli_client 命名空间。
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path

from jiuwenswarm.channels.web.dataagent.analysis.open_analysis import (
    evidence_record,
    file_hash,
)

from . import dwcli_client as _dc
from . import manifest as _mm          # 经模块属性访问：测试打桩 seam（from-import 快照会让 patch 失效）


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _write_csv(rows: list, analysis_id: str, component_id: str,
               columns: list[str] | None = None) -> str:
    """结果落 CSV（path-mode）。execute_sql 的 rows 是「按位置的行值列表」，
    配合 columns 转 dict；dict 行（未来形态）直接用键。"""
    out_dir = _mm._dir(analysis_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", component_id):
        raise SystemExit("[analysis_flow] component_id 格式非法")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = out_dir / f"{component_id}_{ts}.csv"
    if rows and isinstance(rows[0], dict):
        fieldnames = list(rows[0].keys())
        dict_rows = rows
    elif rows and columns:
        fieldnames = list(columns)
        dict_rows = [dict(zip(fieldnames, row)) for row in rows]
    elif columns:
        fieldnames = list(columns)
        dict_rows = []
    else:
        path.write_text("(empty result)\n", "utf-8")
        return str(path)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(dict_rows)
    return str(path)


def _preview(result: dict) -> list[dict]:
    """execute_sql 的位置行转换为结果检查器要求的字典行。"""
    columns = result.get("columns") or []
    return [row if isinstance(row, dict) else dict(zip(columns, row))
            for row in (result.get("rows") or [])[:20]]


def _checked_result(body: dict) -> dict:
    """结果检查不可用时保留执行记录，并明确阻断证据交付。"""
    try:
        outcome = _dc._analysis("analysis", "result-check", body)
    except RuntimeError as exc:
        return {"checks": [{"name": "result_check_available", "status": "fail", "detail": str(exc)}]}
    if not outcome.get("checks"):
        return {"checks": [{"name": "result_check_available", "status": "fail", "detail": "结果检查返回为空"}]}
    return outcome


def _record_evidence(manifest: dict, component_id: str, record: dict, result: dict, *, source_id: str) -> None:
    """统一登记编译/LLM 单个/批量执行证据；结果检查失败不标 done。"""
    record["source_id"] = source_id or (manifest.get("plan") or {}).get("source_id", "")
    record["authoritative"] = bool(result.get("authoritative"))
    record["query_semantics_verified"] = bool(result.get("query_semantics_verified"))
    planned = next((c for c in (manifest.get("plan") or {}).get("components") or []
                    if c["component_id"] == component_id), {})
    record["metric_refs"] = planned.get("selected_metrics") or []
    record["time"] = planned.get("time")
    record["dimensions"] = planned.get("dimensions") or []
    record["artifact_hash"] = file_hash(Path(record["csv_path"]))
    cid_state = _mm._component(manifest, component_id)
    evidence_id = f"ev-{component_id}-{len(cid_state['attempts'])}"
    all_checks = list(result.get("guard") or [])
    all_checks = [{"name": c.get("name") or c.get("check"), **c} for c in all_checks]
    prior = [a for a in cid_state["attempts"] if a.get("kind") == "check" and a.get("sql_hash") == record["sql_hash"]]
    if prior:
        all_checks.extend(prior[-1].get("checks") or [])
    all_checks.extend(record.get("result_checks") or [])
    all_checks.extend(result.get("grounding_checks") or [])
    versions = dict(manifest.get("semantic_versions") or {})
    local_context = cid_state.get("context") or {}
    if local_context.get("semantic_versions"):
        versions.update(local_context["semantic_versions"])
    ev = evidence_record(evidence_id=evidence_id, component_id=component_id, attempt=record,
                         semantic_versions=versions, checks=all_checks)
    manifest.setdefault("evidence", []).append(ev)
    cid_state["evidence_id"] = evidence_id
    if ev["quality"]["status"] == "fail" and manifest.get("mode") == "open":
        if manifest.get("mode") == "open" and not _mm._planned(manifest, component_id)["required"]:
            _mm._skip(manifest, component_id, "result_quality_failed")
        else:
            cid_state["status"] = "failed"
        result.update(ok=False, error="result_quality_failed")
    result["evidence_id"] = evidence_id


def record_execution(manifest: dict, *, analysis_id: str, component_id: str,
                     sql: str, result: dict, kind: str,
                     source_id: str = "", time_window: dict | None = None,
                     record_extra: dict | None = None) -> None:
    """单成分执行后处理管线：成功「CSV→attempt→检查→evidence」，失败记 failed attempt。

    Args:
        kind: attempt 维度（"compile" / "execute"）。
        time_window: 结果检查的时间窗（编译通道取计划 time；生成通道取请求 time）。
        record_extra: attempt 附加字段（编译通道的 query_id/release_id）。
        result: 服务端执行响应，就地富化 csv_path/result_checks/evidence_id。
    """
    component = _mm._component(manifest, component_id)
    if result.get("ok"):
        if manifest.get("mode") != "open":
            budget = manifest.setdefault("budget", {})
            budget["sql_count"] = (budget.get("sql_count") or 0) + 1
        csv_path = _write_csv(result.get("rows") or [], analysis_id, component_id,
                              columns=result.get("columns") or [])
        record = {
            "kind": kind, "at": _mm._now(), "sql": sql,
            "sql_hash": _sql_hash(sql), "csv_path": csv_path,
            "row_count": result.get("row_count"),
            "truncated": result.get("truncated"),
            "grounding_level": result.get("grounding_level"),
            "non_authoritative_reasons": result.get("non_authoritative_reasons"),
            "db_ref": result.get("db_ref"),
        }
        if record_extra:
            record.update(record_extra)
        component["attempts"].append(record)
        component["status"] = "done"
        check_body = {
            "component_id": component_id, "sql": sql,
            "columns": result.get("columns") or [],
            "row_count": result.get("row_count") or 0,
            "truncated": bool(result.get("truncated")),
            "preview_rows": _preview(result),
        }
        if time_window:
            check_body["time"] = time_window
        rc = _checked_result(check_body)
        manifest.setdefault("result_checks", {})[component_id] = rc.get("checks")
        record["result_checks"] = rc.get("checks")
        result["csv_path"] = csv_path
        result["result_checks"] = rc.get("checks")
        _record_evidence(manifest, component_id, record, result, source_id=source_id)
    else:
        record = {
            "kind": kind, "at": _mm._now(), "sql": sql,
            "sql_hash": _sql_hash(sql), "error": result.get("error"),
            "guard": result.get("guard")}
        component["attempts"].append(record)
        if component["status"] != "failed":
            component["status"] = "failed"
        _enforce_retry_limit(manifest, component, kind, record["sql_hash"])


RETRY_HARD_LIMIT = 3


def _enforce_retry_limit(manifest: dict, component: dict, kind: str, sql_hash: str) -> None:
    """同 kind+sql_hash 失败达上限即硬停（d5 重试风暴熔断，实测 17 次同 SQL 重试）。

    相同输入的第 4 次重试不会产生不同结果——与其继续烧轮次，不如强制换
    路径。manifest 先落盘再退出（账本不丢该次失败记录）；组件标
    ``retries_exhausted`` 供评测/追踪页归因。
    """
    fails = [a for a in component["attempts"]
             if a.get("kind") == kind and a.get("sql_hash") == sql_hash
             and a.get("error")]
    if len(fails) < RETRY_HARD_LIMIT:
        return
    component["status"] = "retries_exhausted"
    _mm._save(manifest)
    raise SystemExit(
        f"[retry-limit] 同一 SQL（{kind}）已失败 {len(fails)} 次——相同输入重试"
        "不会再有不同结果。换路径：①改 SQL 口径/时间窗/过滤条件；"
        "②核对表名列名与数据源连通性；③改用其它成分或向用户澄清需求。")
