"""同一份 spec 输出结构一致的 Markdown。"""
from __future__ import annotations

from typing import Any

from render.components import clean_title, fmt_value


def _cell(value: Any) -> str:
    return str(fmt_value(value)).replace("|", "\\|").replace("\n", " ")


def _table(table: dict[str, Any]) -> str:
    columns = [str(column) for column in (table.get("columns") or [])]
    rows = table.get("rows") or []
    if not columns or not rows:
        return ""
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[: int(table.get("max_rows") or 12)]:
        cells = row if isinstance(row, (list, tuple)) else [row.get(column) for column in columns]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def render_markdown(spec: dict[str, Any]) -> str:
    """渲染 Markdown 产物。"""
    parts = [f"# {spec.get('title') or '数据报告'}", ""]
    if spec.get("subtitle"):
        parts += [str(spec["subtitle"]), ""]

    kpis = spec.get("kpis") or []
    if kpis:
        parts += ["## 关键指标", "", "| 指标 | 数值 | 单位 |", "| --- | ---: | --- |"]
        for kpi in kpis:
            parts.append(
                f"| {_cell(kpi.get('label'))} | {_cell(kpi.get('value'))} | {_cell(kpi.get('unit'))} |"
            )
        parts.append("")

    summary = spec.get("summary") or []
    if summary:
        parts += ["## 结论摘要", ""]
        parts += [f"- {_text(item)}" for item in summary]
        parts.append("")

    for index, section in enumerate(spec.get("sections") or [], 1):
        parts += [f"## {index:02d} {clean_title(section.get('title'))}", ""]
        if section.get("conclusion"):
            parts += [f"**{section['conclusion']}**", ""]
        if section.get("chart"):
            caption = section.get("chart_caption") or clean_title(section.get("title"))
            parts += [f"> 图 {index} · {caption}", ""]
        if isinstance(section.get("compare"), dict):
            compare = section["compare"]
            for side in ("a", "b"):
                item = compare.get(side) or {}
                parts.append(
                    f"- **{item.get('label') or ''}**：{item.get('value') or ''} "
                    f"{item.get('unit') or ''}"
                )
            parts.append("")
        table = section.get("_table") or section.get("table") or section.get("diff_table")
        if isinstance(table, dict):
            rendered = _table(table)
            if rendered:
                parts += [rendered, ""]
        narrative = section.get("narrative") or []
        if isinstance(narrative, str):
            narrative = [narrative]
        for paragraph in narrative:
            parts += [str(paragraph), ""]
        alternatives = section.get("alternatives") or []
        if alternatives:
            parts += ["**替代解释检验**", ""]
            for item in alternatives:
                parts.append(
                    f"- [{item.get('verdict') or ''}] **{item.get('label') or ''}**："
                    f"{item.get('text') or ''}"
                )
            parts.append("")
        if section.get("sql"):
            parts += ["```sql", str(section["sql"]), "```", ""]

    if spec.get("insights"):
        parts += ["## 综合洞察", ""]
        parts += [f"- {_text(item)}" for item in spec["insights"]]
        parts.append("")
    if spec.get("actions"):
        parts += ["## 行动建议", ""]
        for item in spec["actions"]:
            text = _text(item)
            impact = item.get("expected_impact") if isinstance(item, dict) else ""
            parts.append(f"- {text}" + (f"（{impact}）" if impact else ""))
        parts.append("")
    if spec.get("limitations"):
        parts += ["## 口径与局限", ""]
        parts += [f"- {_text(item)}" for item in spec["limitations"]]
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
