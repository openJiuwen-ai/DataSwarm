"""同一份 spec 输出结构一致的 Markdown。"""
from __future__ import annotations

from typing import Any

from render.components import clean_title, fmt_value
from render.normalize import as_dicts, as_items, as_text_items, normalize_spec


def _cell(value: Any) -> str:
    return str(fmt_value(value)).replace("|", "\\|").replace("\n", " ")


def _table(table: dict[str, Any]) -> str:
    columns = [str(column) for column in as_items(table.get("columns"))]
    rows = as_items(table.get("rows"))
    if not columns or not rows:
        return ""
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    try:
        max_rows = max(1, int(table.get("max_rows") or 12))
    except (TypeError, ValueError):
        max_rows = 12
    for row in rows[:max_rows]:
        if isinstance(row, dict):
            cells = [row.get(column) for column in columns]
        elif isinstance(row, (list, tuple)):
            cells = row
        else:
            cells = [row]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def render_markdown(spec: dict[str, Any]) -> str:
    """渲染 Markdown 产物。"""
    spec = normalize_spec(spec)
    parts = [f"# {spec.get('title') or '数据报告'}", ""]
    if spec.get("subtitle"):
        parts += [str(spec["subtitle"]), ""]

    kpis = as_dicts(spec.get("kpis"))
    if kpis:
        parts += ["## 关键指标", "", "| 指标 | 数值 | 单位 |", "| --- | ---: | --- |"]
        for kpi in kpis:
            parts.append(
                f"| {_cell(kpi.get('label'))} | {_cell(kpi.get('value'))} | {_cell(kpi.get('unit'))} |"
            )
        parts.append("")

    summary = as_text_items(spec.get("summary"))
    if summary:
        parts += ["## 结论摘要", ""]
        parts += [f"- {_text(item)}" for item in summary]
        parts.append("")

    for index, section in enumerate(as_dicts(spec.get("sections")), 1):
        parts += [f"## {index:02d} {clean_title(section.get('title'))}", ""]
        if section.get("conclusion"):
            parts += [str(section['conclusion']), ""]
        if section.get("chart"):
            caption = section.get("chart_caption") or clean_title(section.get("title"))
            parts += [f"> {caption}（交互图见 HTML 报告，下表为对应数据）", ""]
        if isinstance(section.get("compare"), dict):
            compare = section["compare"]
            for side in ("a", "b"):
                item = compare.get(side)
                if not isinstance(item, dict):
                    continue
                parts.append(
                    f"- **{item.get('label') or ''}**：{_cell(item.get('value'))} "
                    f"{item.get('unit') or ''}"
                )
            parts.append("")
        table = (
            section.get("_table")
            or section.get("table")
            or section.get("diff_table")
            or section.get("_chart_table")
        )
        if isinstance(table, dict):
            rendered = _table(table)
            if rendered:
                parts += [rendered, ""]
                if table is section.get("_chart_table") and len(table.get("rows", [])) > table.get("max_rows", 20):
                    parts += [f"此表展示前 {table.get('max_rows', 20)} 行，共 {len(table['rows'])} 行；完整图表见 HTML 报告。", ""]
        for paragraph in as_text_items(section.get("narrative")):
            parts += [str(paragraph), ""]
        supporting = [_table(table) for table in section.get("supporting_tables", []) if isinstance(table, dict)]
        if any(supporting):
            parts += ["### 补充数据", "", "\n\n".join(filter(None, supporting)), ""]
        alternatives = as_dicts(section.get("alternatives"))
        if alternatives:
            parts += ["**替代解释检验**", ""]
            for item in alternatives:
                parts.append(
                    f"- [{item.get('verdict') or ''}] **{item.get('label') or ''}**："
                    f"{item.get('text') or ''}"
                )
            parts.append("")

    insights = as_text_items(spec.get("insights"))
    if insights:
        parts += ["## 综合洞察", ""]
        for item in insights:
            suffix = (
                "（假设）"
                if isinstance(item, dict)
                and (item.get("hypothesis") or item.get("causal_language") == "driver_hypothesis")
                else ""
            )
            parts.append(f"- {_text(item)}{suffix}")
            if isinstance(item, dict) and item.get("alternative_checked"):
                parts.append("  替代解释检验：" + "；".join(str(v) for v in as_text_items(item["alternative_checked"])))
        parts.append("")
    actions = as_text_items(spec.get("actions"))
    if actions:
        parts += ["## 行动建议", ""]
        for item in actions:
            text = _text(item)
            impact = item.get("expected_impact") if isinstance(item, dict) else ""
            parts.append(f"- {text}" + (f"（{impact}）" if impact else ""))
        parts.append("")
    limitations = as_text_items(spec.get("limitations"))
    if limitations:
        parts += ["## 口径与局限", ""]
        parts += [f"- {_text(item)}" for item in limitations]
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
