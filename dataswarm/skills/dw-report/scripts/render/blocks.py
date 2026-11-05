"""章节级块：判定 block 并渲染。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from render import components as c

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
_MISSING = re.compile(r"\{\{[a-z_]+\}\}")


def render_template(relative: str, **slots: str) -> str:
    """极简模板渲染：{{slot}} 替换，未知占位符报错。"""
    path = TEMPLATES / relative
    text = path.read_text(encoding="utf-8")
    for key, value in slots.items():
        text = text.replace("{{" + key + "}}", value)
    missing = _MISSING.findall(text)
    if missing:
        raise ValueError(f"模板 {relative} 存在未填充占位符: {sorted(set(missing))}")
    return text


def _chart_series_type(chart: Any) -> str:
    if not isinstance(chart, dict):
        return ""
    if isinstance(chart.get("baseOption"), dict):
        chart = chart["baseOption"]
    series = chart.get("series") or []
    if isinstance(series, list) and series and isinstance(series[0], dict):
        return str(series[0].get("type") or "")
    if isinstance(series, dict):
        return str(series.get("type") or "")
    return str(chart.get("type") or "")


def _looks_like_time(chart: Any) -> bool:
    if not isinstance(chart, dict):
        return False
    option = chart.get("baseOption") if isinstance(chart.get("baseOption"), dict) else chart
    axis = option.get("xAxis")
    if isinstance(axis, list) and axis:
        axis = axis[0]
    if not isinstance(axis, dict):
        return False
    values = axis.get("data") or []
    if not values:
        return False
    sample = str(values[0])
    return bool(re.match(r"^\d{4}[-/]\d{1,2}", sample)) or bool(
        re.match(r"^\d{4}年\d{1,2}月", sample)
    )


def detect_block(section: dict[str, Any]) -> str:
    """按优先级判定章节块：显式 > 归因 > 对比 > 表格 > 趋势 > 图表。"""
    explicit = section.get("block")
    if explicit in ("trend", "compare", "attribution", "table", "chart"):
        return explicit
    if (
        section.get("alternative_checked")
        or section.get("alternatives")
        or section.get("drivers")
        or section.get("banner")
        or section.get("claim_type") == "interpretation"
    ):
        return "attribution"
    if section.get("compare") or section.get("diff_table"):
        return "compare"
    title = str(section.get("title") or "")
    if re.search(r"对比|比较|差异|VS|vs", title):
        return "compare"
    if (section.get("_table") or section.get("table")) and not section.get("chart"):
        return "table"
    chart = section.get("chart")
    if _chart_series_type(chart) in ("line", "area") or _looks_like_time(chart):
        return "trend"
    if chart not in (None, "", {}):
        return "chart"
    if section.get("_table") or section.get("table"):
        return "table"
    return "chart"


def render_section(
    section: dict[str, Any],
    index: int,
    chart_option: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    """渲染单个 section，返回 (html, chart_item)。"""
    sid = str(section.get("id") or f"s{index}")
    block = detect_block(section)
    title = c.clean_title(section.get("title"))
    conclusion = c.md_inline(section.get("conclusion") or "")
    chart_id = f"chart-{sid}"
    caption = section.get("chart_caption") or f"图 {index} · {title}"
    # 无图表数据时不留空容器：.chart > div 固定高度，空 figure 会撑出大块空白。
    chart_html = (
        c.chart_figure(chart_id, caption, hero=(block == "trend"))
        if chart_option
        else ""
    )
    sql_html = c.sql_details(section.get("sql"), f"{index:02d}")
    narrative_html = c.narrative(section.get("narrative"))
    chart_item = {"id": chart_id, "option": chart_option} if chart_option else None

    slots = {
        "id": sid,
        "num": f"{index:02d}",
        "title": c.md_inline(title),
        "conclusion": conclusion,
        "chart": chart_html,
        "table": c.table_html(section.get("_table") or section.get("table") or {}),
        "narrative": narrative_html,
        "sql": sql_html,
    }
    if block == "trend":
        slots["annotations"] = c.annotation_cards(section.get("annotations"))
        html = render_template("blocks/trend.html", **slots)
    elif block == "compare":
        slots["compare_head"] = c.compare_head(section)
        slots["diff_table"] = c.diff_table(section)
        html = render_template("blocks/compare.html", **slots)
    elif block == "attribution":
        slots["banner"] = c.banner(section)
        slots["drivers"] = c.drivers(section)
        slots["alternatives"] = c.alternatives(section)
        html = render_template("blocks/attribution.html", **slots)
    elif block == "table":
        slots["table"] = c.table_html(section.get("_table") or section.get("table") or {})
        html = render_template("blocks/table.html", **slots)
    else:
        html = render_template("blocks/chart.html", **slots)
    return html, chart_item
