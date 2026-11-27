"""spec 数据解析：evidence / sql / inline 三种来源统一成可渲染结构。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

_SKILLS_DIR = Path(__file__).resolve().parents[2]
_DW_COMMON = _SKILLS_DIR / "_dw_common" / "scripts"
if str(_DW_COMMON) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON))

# dev 源码形态：从仓库根运行时补 sys.path，保证 report_gate 可导入；
# 运行时形态（skill 在用户目录、jiuwenswarm 已安装）不会命中。
for _parent in Path(__file__).resolve().parents:
    if (_parent / "jiuwenswarm" / "__init__.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from charts.options import ChartRequest, chart_option, json_safe, normalize_option  # noqa: E402
from llm_json import LLMJsonError, load_file  # noqa: E402
from render.normalize import normalize_spec  # noqa: E402
from report_context import bind_analysis_context, has_evidence_references  # noqa: E402


def load_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件（utf-8-sig 兼容 BOM；围栏/语法漂移交给 llm_json 标准化）。"""
    value = load_file(path)
    if not isinstance(value, dict):
        raise LLMJsonError(f"{path}: 顶层必须是对象，实际是 {type(value).__name__}")
    return value


def load_spec(path: str | Path) -> dict[str, Any]:
    """读取报告 spec。"""
    spec = load_json(path)
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象")
    return spec


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(
    spec: dict[str, Any], warnings: list[str], strict: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None, Path | None]:
    raw = spec.get("manifest_path")
    if not raw:
        if (
            spec.get("analysis_id")
            or spec.get("mode") in ("open", "closed")
            or spec.get("report_type") == "analysis"
            or has_evidence_references(spec)
        ):
            raise ValueError("analysis report requires manifest_path")
        return {}, None, None
    path = Path(str(raw)).expanduser()
    if not path.is_file():
        message = f"manifest 不存在，跳过证据校验: {path}"
        raise ValueError(message)
    manifest = load_json(path)
    if spec.get("analysis_id") != manifest.get("analysis_id"):
        raise ValueError("report analysis_id does not match manifest")
    wid = os.environ.get("JIUWENSWARM_WORKSPACE_ID", "").strip()
    sid = os.environ.get("JIUWENSWARM_SESSION_ID", "").strip()
    if wid and manifest.get("workspace_id") != wid:
        raise ValueError("analysis manifest belongs to another workspace or has no verified owner")
    if sid and manifest.get("session_id") != sid:
        raise ValueError("analysis manifest belongs to another session")
    evidence = {
        str(item.get("evidence_id")): item
        for item in manifest.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    return evidence, manifest, path


def _write_json_atomic(path: Path, payload: Any) -> None:
    """原子写 JSON，避免中断留下半截文件。"""
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_evidence_inputs(
    spec: dict[str, Any],
    manifest: dict[str, Any] | None,
    manifest_path: Path | None,
    gate: dict[str, Any] | None,
) -> None:
    """把可重判输入写回 analysis 目录：原始 spec 与带门禁结果的 manifest。

    报告正文之外的重判依据（证据引用、门禁结论）只落文件，不进用户产物。
    """
    if manifest is None or manifest_path is None:
        return
    _write_json_atomic(manifest_path.parent / "report_spec.json", spec)
    if gate is not None:
        _write_json_atomic(manifest_path, {**manifest, "report_gate": gate})


def _read_evidence(
    evidence: dict[str, dict[str, Any]],
    ref: str,
    manifest_dir: Path | None,
    warnings: list[str],
    strict: bool,
) -> pd.DataFrame | None:
    record = evidence.get(ref)
    if not record:
        if strict:
            raise ValueError(f"证据不存在: {ref}")
        return None
    raw = record.get("artifact")
    if not raw:
        if strict:
            raise ValueError(f"证据 {ref} 没有 artifact 路径")
        warnings.append(f"证据 {ref} 没有 artifact 路径")
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute() and manifest_dir:
        path = manifest_dir / path
    path = path.resolve()
    if manifest_dir and not path.is_relative_to(manifest_dir.resolve()):
        raise ValueError(f"证据文件不属于本次分析: {ref}")
    if not path.is_file():
        message = f"证据 CSV 不存在: {path}"
        if strict:
            raise ValueError(message)
        warnings.append(message)
        return None
    expected = record.get("artifact_hash")
    if expected and _sha256(path) != expected:
        raise ValueError(f"证据 CSV 内容已变更: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def _from_sql(db: str, sql: str) -> pd.DataFrame:
    if str(_DW_COMMON) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON))
    import dw_client  # noqa: PLC0415

    result = dw_client.query(db, sql, limit=100000)
    return pd.DataFrame(result.get("rows") or [], columns=result.get("columns") or [])


def _chart_dataframe(
    section: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    db: str | None,
    warnings: list[str],
    strict: bool,
) -> pd.DataFrame | None:
    if evidence:
        from jiuwenswarm.channels.web.dataagent.analysis.report_contract import chart_evidence

        # Older snapshots may omit column metadata. Resolve it from the same
        # bounded, owned, hash-checked CSV before applying the shared rule.
        candidates = {}
        frames = {}
        for ref in section.get("evidence_refs") or []:
            if ref not in evidence:
                continue
            record = evidence[ref]
            if record.get("columns") is None:
                frame = _read_evidence(evidence, ref, manifest_dir, warnings, strict)
                if frame is not None:
                    frames[ref] = frame
                    record = {**record, "columns": list(frame.columns)}
            candidates[ref] = record
        primary, errors = chart_evidence(section, candidates)
        if errors:
            raise ValueError("; ".join(errors))
        if primary:
            section["data_evidence_ref"] = primary
            return (
                frames[primary]
                if primary in frames
                else _read_evidence(evidence, primary, manifest_dir, warnings, strict)
            )
    # SQL derived from analysis lineage is presentation only. Analysis charts
    # must use a bound CSV even when a source db was supplied by the caller.
    if manifest_dir is not None:
        return None
    sql = section.get("sql")
    source = section.get("db") or db
    if sql and source:
        return _from_sql(str(source), str(sql))
    if sql and not source:
        warnings.append(f"section {section.get('title', '')!r} 有 sql 但没有 --db 或 section.db")
    return None


def _resolve_chart(
    section: dict[str, Any],
    index: int,
    inline: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    db: str | None,
    warnings: list[str],
    strict: bool,
) -> dict[str, Any] | None:
    chart = section.get("chart")
    if chart in (None, "", {}):
        return None
    if isinstance(chart, str):
        option = (inline.get("charts") or {}).get(chart)
        if option is None:
            warnings.append(f"sections[{index}] 的 chart id 在 data.json 中不存在: {chart}")
            return None
        return normalize_option(option)
    if not isinstance(chart, dict):
        warnings.append(f"sections[{index}].chart 类型不支持")
        return None
    if "baseOption" in chart or "series" in chart:
        return normalize_option(chart)
    frame = _chart_dataframe(section, evidence, manifest_dir, db, warnings, strict)
    if frame is None:
        warnings.append(f"sections[{index}] 需要从 CSV 构图，但拿不到数据（缺 manifest / --db / data.json）")
        return None
    frame = _chart_view(frame, chart.get("data_view") or {})
    request = ChartRequest.from_mapping(chart)
    # Markdown cannot host ECharts; show the exact selected chart data instead.
    columns = list(
        dict.fromkeys([request.category, *request.measures, *([request.split_by] if request.split_by else [])])
    )
    section["_chart_table"] = {"columns": columns, "rows": json_safe(frame[columns].to_dict(orient="records")),
                               "max_rows": 20}
    return chart_option(frame, request)


def _chart_view(frame: pd.DataFrame, view: dict) -> pd.DataFrame:
    """Select chart rows from verified data without querying, aggregating or evaluating code."""
    selected = frame.copy()
    for condition in view.get("filters", []):
        field, operation = condition["field"], condition["op"]
        if field not in selected.columns:
            raise ValueError(f"chart.data_view filter column does not exist: {field}")
        values = condition["values"]
        mask = selected[field].isin(values)
        if operation == "not_in":
            mask = ~mask
        elif operation != "in":
            raise ValueError("chart.data_view filter op must be in or not_in")
        selected = selected[mask]
    order = view.get("sort")
    if order:
        field = order["field"]
        if field not in selected.columns:
            raise ValueError(f"chart.data_view sort column does not exist: {field}")
        selected = selected.sort_values(field, ascending=not order.get("descending", True), kind="stable")
    if "limit" in view:
        selected = selected.head(view["limit"])
    if selected.empty:
        raise ValueError("chart.data_view selected no rows; correct the view using existing evidence")
    return selected


def _select_rows(frame: pd.DataFrame, selector: Any, index: int, warnings: list[str]) -> pd.DataFrame:
    if isinstance(selector, dict):
        mask = pd.Series(True, index=frame.index)
        for key, value in selector.items():
            if key not in frame.columns:
                warnings.append(f"kpis[{index}].row 引用的列不存在: {key}")
                return frame.iloc[0:0]
            mask &= frame[key].astype(str) == str(value)
        return frame[mask]
    if isinstance(selector, int):
        return frame.iloc[[selector]]
    return frame


def _resolve_kpis(
    kpis: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    manifest_dir: Path | None,
    warnings: list[str],
    strict: bool,
) -> None:
    for index, kpi in enumerate(kpis):
        column = kpi.get("value_column")
        refs = kpi.get("evidence_refs") or []
        if not column or not refs:
            continue
        primary = kpi.get("data_evidence_ref") or (refs[0] if len(refs) == 1 else None)
        if primary is None:
            raise ValueError(f"kpis[{index}] 多份证据需明确data_evidence_ref")
        frame = _read_evidence(evidence, primary, manifest_dir, warnings, strict)
        if frame is None or column not in frame.columns or frame.empty:
            warnings.append(f"kpis[{index}] 无法从证据读取 {column!r}")
            continue
        frame = _select_rows(frame, kpi.get("row"), index, warnings)
        if frame.empty:
            warnings.append(f"kpis[{index}].row 没有匹配行")
            continue
        if len(frame) > 1:
            raise ValueError(f"kpis[{index}] 证据匹配多行；用row明确定位，不能默认取最后一行")
        value = json_safe(frame.iloc[-1][column])
        if kpi.get("value") is not None and str(kpi["value"]) != str(value):
            warnings.append(f"kpis[{index}] 的 value 与证据不一致，已按证据覆盖为 {value!r}")
        kpi["value"] = value


def resolve(
    spec: dict[str, Any],
    data_path: str | Path | None = None,
    db: str | None = None,
    strict: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    """解析 spec，返回带内部字段的副本。

    Returns:
        原 spec 的深拷贝，额外带 ``_charts``（块 id → option）与 ``_warnings``。
    """
    warnings: list[str] = []
    resolved = bind_analysis_context(spec)
    inline = load_json(data_path) if data_path else {}
    evidence, manifest, manifest_path = _load_manifest(resolved, warnings, strict)
    strict = strict or manifest is not None
    manifest_dir = manifest_path.parent if manifest_path else None
    if manifest is not None:
        from jiuwenswarm.channels.web.dataagent.analysis.report_validation import (
            chart_requirement_errors,
            minimum_charts,
            report_gate,
        )
        from jiuwenswarm.channels.web.dataagent.analysis.report_contract import normalize_evidence_refs

        if manifest.get("protocol_version") != 1:
            raise ValueError("analysis snapshot must use the current task protocol")
        resolved, changes, normalization_errors = normalize_evidence_refs(resolved, manifest)
        if normalization_errors:
            raise ValueError("; ".join(normalization_errors))
        disclosures = [f"口径选择：{d['issue']}；本次采用：{d['selected']}；依据：{d['reason']}；"
                       f"其他解释：{'、'.join(c for c in d['candidates'] if c != d['selected'])}；"
                       f"影响：{d['impact']}" for d in manifest.get("assumptions", {}).values()]
        raw_limitations = resolved.get("limitations") or []
        resolved["limitations"] = ([raw_limitations] if isinstance(raw_limitations, str)
                                    else list(raw_limitations)) + disclosures
        from jiuwenswarm.channels.web.dataagent.analysis.perspectives import coverage

        breadth = coverage(manifest, resolved.get("sections", []))
        resolved["limitations"] += ["章节关联未确认的分析视角：" + item["question"] for item in breadth["items"]
                                     if item["required"] and item["status"] != "answered"]
        resolved["limitations"] += ["取数任务未回答：" + question for task in manifest.get("tasks", {}).values()
                                     for question in (task.get("completion") or {}).get("remaining_questions", [])]
        from jiuwenswarm.channels.web.dataagent.analysis.report_contract import EVIDENCE_FIELDS

        refs = {ref for field in EVIDENCE_FIELDS for item in resolved.get(field, [])
                if isinstance(item, dict) for ref in item.get("evidence_refs", [])}
        from jiuwenswarm.channels.web.dataagent.analysis.evidence import available

        usable_evidence = available(manifest)
        cited = [task for eid, task in usable_evidence.items() if eid in refs]
        for task in cited:
            resolved["limitations"].extend(task["result"].get("limitations", []))
        packs = (manifest.get("context") or {}).get("packs", {})
        metric_keys = {key for task in cited for field in ("selected_metrics", "reference_metrics")
                       for key in task.get("spec", {}).get(field, [])}
        metric_keys.update(key for task in cited for key in task["result"].get("selected_metrics", []))
        units = sorted({str(m.get("unit") or (m.get("definition") or {}).get("unit") or "") for m in
                        packs.get("metrics_candidates", []) + packs.get("metrics_selected", [])
                        if m.get("metric_key") in metric_keys} - {""})
        authoritative = [key for key, task in usable_evidence.items() if task["result"].get("authoritative")]
        exploratory = [key for key, task in usable_evidence.items() if not task["result"].get("authoritative")]
        if authoritative:
            resolved["limitations"].append("权威口径：已发布指标经确定性编译执行（任务 " + "、".join(authoritative) + "）。")
        if exploratory:
            resolved["limitations"].append("探索口径：模型直接生成 SQL，经执行检查但不具备已发布指标编译权威性（任务 " + "、".join(exploratory) + "）。")
        if units:
            resolved["limitations"].append("指标单位以已发布定义为准：" + "、".join(units))
        resolved["limitations"].append("本报告证据来自查询结果；维度相关与指标分解不能单独证明因果关系。")
        gate = report_gate(resolved, manifest, evidence_root=manifest_dir)
        resolved["status"] = gate["status"]
        resolved["limitations"] = list(dict.fromkeys([*resolved["limitations"], *gate.get("warnings", [])]))
        gate["normalized_refs"] = changes
        resolved["report_gate"] = gate
        if gate.get("ok"):
            presentation_errors = chart_requirement_errors(resolved, manifest)
            if presentation_errors:
                raise ValueError("; ".join(presentation_errors))
            resolved["_min_charts"] = minimum_charts(resolved, manifest)
            from jiuwenswarm.channels.web.dataagent.analysis.report_numbers import fill_numbers

            resolved = fill_numbers(resolved, gate["numeric_check"])
        if persist:
            _persist_evidence_inputs(copy.deepcopy(resolved), manifest, manifest_path, gate)
        if not gate.get("ok"):
            raise ValueError("report_gate failed: " + json.dumps(gate, ensure_ascii=False))
        refs = {
            ref
            for field in ("sections", "kpis", "summary", "insights", "actions")
            for item in resolved.get(field, [])
            for ref in item["evidence_refs"]
        }
        for ref in refs:
            _read_evidence(evidence, ref, manifest_dir, warnings, True)
    resolved = normalize_spec(resolved, warnings)

    charts: dict[str, dict[str, Any]] = {}
    for index, section in enumerate(resolved.get("sections") or [], 1):
        sid = str(section.get("id") or f"s{index}")
        if evidence:
            refs = section.get("evidence_refs") or []
            from provenance import section_lineage

            section["sql"], _methods = section_lineage(refs, evidence)
        section.pop("computation_method", None)
        option = _resolve_chart(section, index, inline, evidence, manifest_dir, db, warnings, strict)
        if option:
            charts[sid] = option
        table = section.get("table")
        if not isinstance(table, dict):
            table_id = section.get("table_id")
            table = (inline.get("tables") or {}).get(table_id) if table_id else None
        if isinstance(table, dict):
            section["_table"] = table

    if manifest is None:
        _resolve_kpis(resolved.get("kpis") or [], evidence, manifest_dir, warnings, strict)
    if manifest and manifest_path is not None and persist:
        resolved["_evidence_spec_hash"] = _sha256(manifest_path.parent / "report_spec.json")
    if len(charts) < resolved.get("_min_charts", 0):
        raise ValueError("分析报告至少需要1张实际生成的有效图表；请修正图表数据绑定。")
    resolved["_charts"] = charts
    resolved["_warnings"] = warnings
    return resolved
