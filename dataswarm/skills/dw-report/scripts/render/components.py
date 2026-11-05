"""HTML 片段组件。所有函数只返回字符串，不读写文件。"""
from __future__ import annotations

import html
import re
from typing import Any, Iterable


def esc(value: Any) -> str:
    """HTML 转义。"""
    return html.escape("" if value is None else str(value), quote=True)


_NUMBER_PREFIX = re.compile(r"^[一二三四五六七八九十百]+、\s*")


def clean_title(value: Any) -> str:
    """去掉标题自带的「一、」编号。"""
    return _NUMBER_PREFIX.sub("", str(value or "")).strip()


def md_inline(value: Any) -> str:
    """转义后支持 **粗体** 与 `代码`。"""
    text = esc(value)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def fmt_value(value: Any) -> str:
    """数值展示：千分位、去尾零。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    return esc(value)


def _text_items(items: Iterable[Any]) -> list[Any]:
    return [item for item in (items or []) if item not in (None, "")]


def hero(spec: dict[str, Any]) -> str:
    """标题区：eyebrow + 标题 + 副标题 + 元信息。"""
    title = esc(spec.get("title") or "数据报告")
    subtitle = spec.get("subtitle")
    eyebrow = spec.get("eyebrow") or "DATA ANALYSIS"
    parts = [
        '<header class="hero"><div class="hero-main">',
        f'<div class="eyebrow">{esc(eyebrow)}</div>',
        f"<h1>{title}</h1>",
    ]
    if subtitle:
        parts.append(f'<p class="dek">{md_inline(subtitle)}</p>')
    parts.append("</div>")
    meta = []
    if spec.get("period"):
        meta.append(f"<span>{esc(spec['period'])}</span>")
    if spec.get("data_source"):
        meta.append(f"<span>{esc(spec['data_source'])}</span>")
    if spec.get("generated_at"):
        meta.append(f"<span>{esc(spec['generated_at'])}</span>")
    if meta:
        parts.append(f'<div class="meta">{"".join(meta)}</div>')
    parts.append("</header>")
    return "".join(parts)


def kpis(items: Iterable[dict[str, Any]]) -> str:
    """KPI 条。"""
    cards = []
    for item in _text_items(items):
        if not isinstance(item, dict):
            continue
        label = esc(item.get("label") or "")
        value = fmt_value(item.get("value"))
        unit = item.get("unit") or ""
        tone = item.get("tone") or "flat"
        delta = item.get("delta")
        card = [
            '<div class="kpi">',
            f'<div class="kpi-label">{label}</div>',
            f'<div class="kpi-value">{value}'
            + (f'<span class="unit">{esc(unit)}</span>' if unit else "")
            + "</div>",
        ]
        if delta:
            card.append(f'<div class="kpi-delta {esc(tone)}">{esc(delta)}</div>')
        card.append("</div>")
        cards.append("".join(card))
    return f'<section class="kpis">{"".join(cards)}</section>' if cards else ""


def bluf(summary: Iterable[Any]) -> str:
    """结论摘要（BLUF）。"""
    items = []
    for item in _text_items(summary):
        text = item.get("text") if isinstance(item, dict) else item
        if text:
            items.append(f"<li>{md_inline(text)}</li>")
    if not items:
        return ""
    return (
        '<div class="bluf"><h3>结论摘要</h3><ol>'
        + "".join(items[:5])
        + "</ol></div>"
    )


def toc(sections: Iterable[dict[str, Any]]) -> str:
    """章节锚点。"""
    links = []
    for index, section in enumerate(sections, 1):
        sid = str(section.get("id") or f"s{index}")
        title = clean_title(section.get("title") or f"第 {index} 节")
        label = section.get("short") or (title if len(title) <= 14 else title[:14] + "…")
        links.append(f'<a href="#{esc(sid)}">{index:02d} {esc(label)}</a>')
    if len(links) < 2:
        return ""
    return f'<nav class="toc">{"".join(links)}</nav>'


def narrative(items: Any) -> str:
    """解读段落。"""
    if not items:
        return ""
    if isinstance(items, str):
        items = [items]
    blocks = [f"<p>{md_inline(item)}</p>" for item in items if item]
    return f'<div class="narrative">{"".join(blocks)}</div>' if blocks else ""


def sql_details(sql: Any, label: str) -> str:
    """可折叠 SQL。"""
    if not sql:
        return ""
    return (
        f'<details class="sql"><summary>SQL · {esc(label)}</summary>'
        f"<pre>{esc(sql)}</pre></details>"
    )


def chart_figure(chart_id: str, caption: str, hero: bool = False) -> str:
    """图表容器。"""
    if not chart_id:
        return ""
    klass = "chart chart-hero" if hero else "chart"
    return (
        f'<figure class="{klass}"><div id="{esc(chart_id)}"></div>'
        f"<figcaption>{md_inline(caption)}</figcaption></figure>"
    )


def table_html(table: dict[str, Any]) -> str:
    """明细表。支持 rows 为二维数组或对象数组。"""
    if not isinstance(table, dict):
        return ""
    columns = [str(column) for column in (table.get("columns") or [])]
    rows = table.get("rows") or []
    if not columns and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    if not columns:
        return ""
    max_rows = int(table.get("max_rows") or 12)
    shown = rows[:max_rows]
    body = []
    for row in shown:
        cells = row if isinstance(row, (list, tuple)) else [row.get(column) for column in columns]
        body.append("<tr>" + "".join(f"<td>{fmt_value(cell)}</td>" for cell in cells) + "</tr>")
    head = "".join(f"<th>{esc(column)}</th>" for column in columns)
    note = ""
    if len(rows) > max_rows:
        note = f'<p class="narrative">仅展示前 {max_rows} 行，共 {len(rows)} 行。</p>'
    return (
        f'<table class="diff-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>{note}'
    )


def block(title: str, body: str, klass: str = "") -> str:
    """尾部信息块。"""
    if not body:
        return ""
    return f'<section class="block {esc(klass)}"><h3>{esc(title)}</h3>{body}</section>'


def insights(items: Iterable[Any]) -> str:
    """综合洞察；对象形态保留「假设」与「已排除的替代解释」。"""
    lis = []
    for item in _text_items(items):
        if isinstance(item, dict):
            text = item.get("text")
            hypothesis = item.get("hypothesis")
            alternatives = item.get("alternative_checked") or []
        else:
            text, hypothesis, alternatives = item, False, []
        if not text:
            continue
        content = md_inline(text)
        if hypothesis:
            content += ' <span class="tag">假设</span>'
        if alternatives:
            joined = "；".join(str(value) for value in alternatives)
            content += f'<div class="alt-note">已排除的替代解释：{md_inline(joined)}</div>'
        lis.append(f"<li>{content}</li>")
    return block("综合洞察", f"<ul>{''.join(lis)}</ul>") if lis else ""


def actions(items: Iterable[Any], title: str = "行动建议") -> str:
    """行动建议（量化预期用 chip）。"""
    lis = []
    for item in _text_items(items):
        if isinstance(item, dict):
            text = item.get("text") or ""
            impact = item.get("expected_impact") or ""
        else:
            text, impact = item, ""
        if not text:
            continue
        chip = f'<span class="impact">{esc(impact)}</span>' if impact else ""
        lis.append(f"<li><strong>{md_inline(text)}</strong>{chip}</li>")
    return block(title, f"<ul>{''.join(lis)}</ul>", "actions") if lis else ""


def limitations(items: Iterable[Any]) -> str:
    """口径与局限。"""
    lis = "".join(f"<li>{md_inline(item)}</li>" for item in _text_items(items))
    return block("口径与局限", f"<ul>{lis}</ul>", "limitations") if lis else ""


def footer(spec: dict[str, Any]) -> str:
    """页脚。"""
    source = spec.get("data_source") or "dw-report"
    generated = spec.get("generated_at") or ""
    return (
        f'<footer class="foot"><span>{esc(source)}</span>'
        f"<span>{esc(generated)} · dw-report</span></footer>"
    )


def annotation_cards(items: Iterable[Any]) -> str:
    """趋势块的时间锚点卡。"""
    cards = []
    for item in _text_items(items):
        if not isinstance(item, dict):
            continue
        cards.append(
            '<div class="annotation">'
            f'<span class="date">{esc(item.get("date") or "")}</span>'
            f'<strong>{md_inline(item.get("title") or "")}</strong>'
            f'<p>{md_inline(item.get("text") or "")}</p></div>'
        )
    return f'<section class="annotations">{"".join(cards)}</section>' if cards else ""


def compare_head(section: dict[str, Any]) -> str:
    """对比块的双栏头。"""
    compare = section.get("compare")
    if not isinstance(compare, dict):
        return ""
    cols = []
    for side in ("a", "b"):
        item = compare.get(side)
        if not isinstance(item, dict):
            continue
        rows = "".join(f"<li>{md_inline(text)}</li>" for text in item.get("items") or [])
        cols.append(
            '<div class="compare-col">'
            f'<span class="col-label">{esc(item.get("label") or "")}</span>'
            f'<span class="big">{esc(item.get("value") or "")}'
            + (f'<small style="font-size:13px;color:#8A94A6">{esc(item.get("unit") or "")}</small>'
               if item.get("unit") else "")
            + f"</span><ul>{rows}</ul></div>"
        )
    if len(cols) != 2:
        return ""
    return f'<section class="compare-head">{cols[0]}<div class="vs">VS</div>{cols[1]}</section>'


def diff_table(section: dict[str, Any]) -> str:
    """对比块的差异表。"""
    table = section.get("diff_table")
    return table_html(table) if isinstance(table, dict) else ""


def banner(section: dict[str, Any]) -> str:
    """归因块的异常信号横幅。"""
    data = section.get("banner")
    if not isinstance(data, dict):
        return ""
    return (
        '<div class="banner"><div>'
        f'<span class="banner-label">{esc(data.get("label") or "异常信号")}</span>'
        f'<strong>{esc(data.get("value") or "")}</strong></div>'
        f'<div class="banner-flow">{md_inline(data.get("flow") or "")}</div></div>'
    )


def drivers(section: dict[str, Any]) -> str:
    """归因块的驱动卡。"""
    items = _text_items(section.get("drivers"))
    if not items:
        return ""
    cards = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tag = f'<span class="tag">{esc(item.get("tag"))}</span>' if item.get("tag") else ""
        cards.append(
            '<div class="driver">'
            f'<span class="share">{esc(item.get("share") or "")}</span>'
            f'<strong>{md_inline(item.get("label") or "")}</strong>'
            f'<p>{md_inline(item.get("value") or "")}</p>{tag}</div>'
        )
    return f'<section class="drivers">{"".join(cards)}</section>' if cards else ""


def alternatives(section: dict[str, Any]) -> str:
    """归因块的替代解释检验。"""
    items = _text_items(section.get("alternatives"))
    if not items:
        items = []
        for raw in section.get("alternative_checked") or []:
            text = str(raw)
            separator = "：" if "：" in text else (":" if ":" in text else "")
            if separator:
                label, detail = text.split(separator, 1)
            else:
                label, detail = "替代解释", text
            items.append({"verdict": "已检验", "label": label, "text": detail})
    if not items:
        return ""
    classes = {"排除": "reject", "部分": "partial", "支持": "support", "已检验": "partial"}
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict") or "")
        klass = classes.get(verdict, "partial")
        rows.append(
            '<div class="alt-row">'
            f'<span class="verdict {klass}">{esc(verdict)}</span>'
            f'<div><strong>{md_inline(item.get("label") or "")}</strong>'
            f'<p>{md_inline(item.get("text") or "")}</p></div></div>'
        )
    return "".join(rows)
