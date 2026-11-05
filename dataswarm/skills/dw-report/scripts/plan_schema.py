"""报告 spec 校验：必填字段尽量少，未知字段透传。"""
from __future__ import annotations

from typing import Any

BLOCKS = ("trend", "compare", "attribution", "table", "chart")
CHART_TYPES = ("line", "bar", "pie", "scatter", "heatmap")


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(spec: Any) -> list[str]:
    """校验 spec，返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec 必须是 JSON 对象"]

    if not _is_text(spec.get("title")):
        errors.append("title: 必填的非空字符串")

    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("sections: 必填且至少 1 节")
        return errors

    mode = spec.get("mode")
    if mode == "open":
        if not _is_text(spec.get("analysis_id")):
            errors.append("mode=open 时 analysis_id 必填")
        if not _is_text(spec.get("manifest_path")):
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
                if "baseOption" in chart or "series" in chart:
                    pass  # 完整 option
                elif chart.get("type"):
                    if chart["type"] not in CHART_TYPES:
                        errors.append(
                            f"{prefix}.chart.type: {chart['type']!r} 不支持，"
                            f"可用 {list(CHART_TYPES)}"
                        )
                    if not chart.get("x"):
                        errors.append(f"{prefix}.chart.x: 必填")
                    if not chart.get("y"):
                        errors.append(f"{prefix}.chart.y: 必填")
                else:
                    errors.append(f"{prefix}.chart: 必须是 option、{list(CHART_TYPES)} 规格或 chart id")
            else:
                errors.append(f"{prefix}.chart: 类型不支持")

        if block == "compare" and not (
            isinstance(section.get("compare"), dict)
            or isinstance(section.get("diff_table"), dict)
            or isinstance(chart, dict)
        ):
            errors.append(f"{prefix}: compare 块需要 compare / diff_table 或 chart")

        if block == "attribution" and not isinstance(section.get("chart"), dict):
            errors.append(f"{prefix}: attribution 块需要 chart（拆解图）")

    return errors
