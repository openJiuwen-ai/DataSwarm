"""页面级判定与页面壳渲染。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from render import components as c
from render.blocks import render_section, render_template


def detect_page(spec: dict[str, Any]) -> str:
    """判定页面类型：answer / brief / analysis。"""
    sections = spec.get("sections") or []
    kpis = spec.get("kpis") or []
    summary = spec.get("summary") or []
    if len(sections) == 1 and (
        spec.get("shape") in ("chart", "answer") or (not kpis and not summary)
    ):
        return "answer"
    has_chart = any(section.get("chart") not in (None, "", {}) for section in sections)
    if spec.get("shape") == "lite" and len(sections) <= 3:
        return "brief"
    if len(sections) <= 2 and len(kpis) <= 3 and not spec.get("insights") and not has_chart:
        return "brief"
    return "analysis"


def _section_chart(section: dict[str, Any], index: int, charts: dict[str, Any]) -> Any:
    sid = str(section.get("id") or f"s{index}")
    return charts.get(sid)


def _analysis_body(
    spec: dict[str, Any], charts: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    sections = spec.get("sections") or []
    cards: list[str] = []
    chart_items: list[dict[str, Any]] = []
    for index, section in enumerate(sections, 1):
        html, item = render_section(section, index, _section_chart(section, index, charts))
        cards.append(html)
        if item:
            chart_items.append(item)
    body = render_template(
        "pages/analysis.html",
        hero=c.hero(spec),
        kpis=c.kpis(spec.get("kpis") or []),
        bluf=c.bluf(spec.get("summary") or []),
        toc=c.toc(sections),
        sections="".join(cards),
        insights=c.insights(spec.get("insights") or []),
        actions=c.actions(spec.get("actions") or []),
        limitations=c.limitations(spec.get("limitations") or []),
        footer=c.footer(spec),
    )
    return body, chart_items


def _brief_body(
    spec: dict[str, Any], charts: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    sections = spec.get("sections") or []
    rows = []
    for index, section in enumerate(sections[:4], 1):
        delta = section.get("delta")
        tone = section.get("tone") or "up"
        delta_html = f'<span class="delta {tone}">{c.esc(delta)}</span>' if delta else ""
        rows.append(
            "<li>"
            f'<span class="idx">{index:02d}</span>'
            f"<div><strong>{c.md_inline(section.get('title') or '')}</strong>"
            f"<p>{c.md_inline(section.get('conclusion') or '')}</p></div>"
            f"{delta_html}</li>"
        )
    brief_html = (
        '<section class="block"><h3>本周三件事</h3>'
        f'<ol class="brief-list">{"".join(rows)}</ol></section>'
        if rows else ""
    )
    callout = spec.get("callout")
    callout_html = (
        f'<div class="callout"><strong>需要关注</strong>　{c.md_inline(callout)}</div>'
        if callout else ""
    )
    body = render_template(
        "pages/brief.html",
        hero=c.hero(spec),
        kpis=c.kpis(spec.get("kpis") or []),
        brief_body=brief_html,
        callout=callout_html,
        actions=c.actions(spec.get("actions") or [], title="下周动作"),
        footer=c.footer(spec),
    )
    return body, []


def _answer_body(
    spec: dict[str, Any], charts: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    sections = spec.get("sections") or []
    section = sections[0] if sections else {}
    sid = str(section.get("id") or "s1")
    option = charts.get(sid)
    chart_id = f"chart-{sid}"
    chart_html = c.chart_figure(chart_id, f"图 1 · {section.get('title') or ''}", hero=True)
    answer_html = (
        f'<section class="sec"><p class="conclusion">{c.md_inline(section.get("conclusion") or "")}</p>'
        f"{chart_html}"
        f"{c.narrative(section.get('narrative'))}"
        f"{c.sql_details(section.get('sql'), '01')}</section>"
    )
    body = render_template(
        "pages/answer.html",
        hero=c.hero(spec),
        answer_body=answer_html,
        footer=c.footer(spec),
    )
    item = {"id": chart_id, "option": option} if option else None
    return body, ([item] if item else [])


def chart_script(items: list[dict[str, Any]]) -> str:
    """图表初始化脚本（ES5）。"""
    if not items:
        return ""
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    return (
        "var CHARTS = " + payload + ";\n"
        "var instances = CHARTS.map(function (item) {"
        "var el = document.getElementById(item.id); if (!el) { return null; }"
        "var chart = echarts.init(el); chart.setOption(item.option); return chart;"
        "}).filter(function (chart) { return chart !== null; });\n"
        "window.addEventListener('resize', function () {"
        "instances.forEach(function (chart) { chart.resize(); }); });\n"
    )


def render_html(spec: dict[str, Any], charts: dict[str, Any], assets: Path) -> str:
    """渲染完整 HTML（ECharts 仍为占位符，由 inline_assets 替换）。"""
    spec.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    page = detect_page(spec)
    if page == "answer":
        body, items = _answer_body(spec, charts)
    elif page == "brief":
        body, items = _brief_body(spec, charts)
    else:
        body, items = _analysis_body(spec, charts)
    css = (assets / "report.css").read_text(encoding="utf-8")
    js = (assets / "report.js").read_text(encoding="utf-8")
    return render_template(
        "base.html",
        title=c.esc(spec.get("title") or "数据报告"),
        echarts='<script src="__ECHARTS__"></script>',
        css=css,
        body=body,
        body_class=f"page-{page}",
        js=js + "\n" + chart_script(items),
    )
