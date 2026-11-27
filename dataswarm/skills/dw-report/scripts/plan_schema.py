"""报告 spec 校验：必填字段尽量少，未知字段透传。"""

from __future__ import annotations

from typing import Any

from report_context import bind_analysis_context, context_errors, requires_evidence

BLOCKS = ("trend", "compare", "attribution", "table", "chart")
CHART_TYPES = ("line", "bar", "pie", "scatter", "heatmap")


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(spec: Any, *, draft: bool = False) -> list[str]:
    """校验 spec，返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec 必须是 JSON 对象"]
    errors.extend(context_errors(spec))
    try:
        bound = bind_analysis_context(spec, allow_unbound=draft)
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
        bound = spec
    if requires_evidence(bound):
        manifest = None
        from jiuwenswarm.channels.web.dataagent.analysis.report_contract import (
            evidence_structure_errors,
            normalize_evidence_refs,
        )
        from report_context import _read_object

        if bound.get("manifest_path"):
            from pathlib import Path

            try:
                manifest = _read_object(Path(bound["manifest_path"]))
                bound, _changes, normalization_errors = normalize_evidence_refs(bound, manifest)
                errors.extend(normalization_errors)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        else:
            bound, _changes, normalization_errors = normalize_evidence_refs(bound, {"tasks": {}})
            errors.extend(normalization_errors)
        errors.extend(evidence_structure_errors(bound, state=manifest))

    spec = bound
    if not _is_text(spec.get("title")):
        errors.append("title: 必填的非空字符串")

    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("sections: 必填且至少 1 节")
        return errors

    mode = spec.get("mode")
    if mode == "open" and not draft:
        if not _is_text(bound.get("analysis_id")):
            errors.append("mode=open 时 analysis_id 必填")
        if not _is_text(bound.get("manifest_path")):
            errors.append("mode=open 时 manifest_path 必填")

    kpis = spec.get("kpis")
    if kpis is not None and not isinstance(kpis, list):
        errors.append("kpis: 必须是数组")
    for index, kpi in enumerate(kpis or []):
        if not isinstance(kpi, dict):
            errors.append(f"kpis[{index}]: 必须是对象")
            continue
        if not _is_text(kpi.get("label")):
            errors.append(f"kpis[{index}].label: 必填")

    # 文本列表字段：允许字符串（渲染层按单条兼容）或数组，其余类型大声失败。
    for field in ("summary", "insights", "actions", "limitations"):
        value = spec.get(field)
        if value is not None and not isinstance(value, (str, list)):
            errors.append(f"{field}: 必须是字符串或数组")

    for index, section in enumerate(sections):
        prefix = f"sections[{index}]"
        if not isinstance(section, dict):
            errors.append(f"{prefix}: 必须是对象")
            continue
        for field in ("title", "conclusion"):
            if not _is_text(section.get(field)):
                errors.append(f"{prefix}.{field}: 必填的非空字符串")
        narrative = section.get("narrative")
        if narrative is not None and not isinstance(narrative, (str, list)):
            errors.append(f"{prefix}.narrative: 必须是字符串或字符串数组")

        block = section.get("block")
        if block is not None and block not in BLOCKS:
            errors.append(f"{prefix}.block: 必须是 {list(BLOCKS)} 之一")

        chart = section.get("chart")
        if chart is not None and chart != "":
            if isinstance(chart, str):
                pass  # data.json 的 chart id
            elif isinstance(chart, dict):
                view = chart.get("data_view")
                if view is not None:
                    if not isinstance(view, dict):
                        errors.append(f"{prefix}.chart.data_view: 必须是对象")
                    else:
                        if "series" in chart or "baseOption" in chart:
                            errors.append(f"{prefix}.chart.data_view: 只用于引用CSV的type/x/y图表")
                        filters = view.get("filters", [])
                        if not isinstance(filters, list) or any(
                            not isinstance(f, dict) or not _is_text(f.get("field"))
                            or f.get("op") not in ("in", "not_in") or not isinstance(f.get("values"), list)
                            or not f["values"] or any(isinstance(v, (dict, list)) for v in f["values"])
                            for f in filters
                        ):
                            errors.append(f"{prefix}.chart.data_view.filters: 使用field/op(in或not_in)/values数组")
                        order = view.get("sort")
                        if order is not None and (not isinstance(order, dict) or not _is_text(order.get("field"))
                                                  or not isinstance(order.get("descending", True), bool)):
                            errors.append(f"{prefix}.chart.data_view.sort: 使用field和descending布尔值")
                        if "limit" in view and (type(view["limit"]) is not int or not 1 <= view["limit"] <= 500):
                            errors.append(f"{prefix}.chart.data_view.limit: 必须为1到500的整数")
                if "baseOption" in chart or "series" in chart:
                    pass  # 完整 option
                elif chart.get("type"):
                    if chart["type"] not in CHART_TYPES:
                        errors.append(f"{prefix}.chart.type: {chart['type']!r} 不支持，可用 {list(CHART_TYPES)}")
                    if not chart.get("x"):
                        errors.append(f"{prefix}.chart.x: 必填")
                    if not chart.get("y"):
                        errors.append(f"{prefix}.chart.y: 必填")
                else:
                    errors.append(f"{prefix}.chart: 必须是 option、{list(CHART_TYPES)} 规格或 chart id")
            else:
                errors.append(f"{prefix}.chart: 类型不支持")

    return errors
