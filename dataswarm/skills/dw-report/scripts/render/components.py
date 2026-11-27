"""HTML 片段组件。所有函数只返回字符串，不读写文件。"""
from __future__ import annotations

import html
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any, Iterable

from render.normalize import as_dicts, as_items, as_text_items


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


_DECIMAL_VALUE = re.compile(
    r"^[+-]?(?:(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)\.\d+"
    r"(?:[eE][+-]?\d+)?|(?:0|[1-9]\d*)[eE][+-]?\d+)(%|％)?$"
)


def fmt_value(value: Any) -> str:
    """展示数值最多两位小数、去尾零；极小非零值保留三位有效数字。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    suffix = ""
    if isinstance(value, str):
        match = _DECIMAL_VALUE.fullmatch(value.strip())
        if not match:
            return esc(value)
        suffix = match.group(1) or ""
        value = value.strip().removesuffix(suffix).replace(",", "")
    elif not isinstance(value, (int, float, Decimal)):
        return esc(value)
    number = Decimal(str(value))
    if not number.is_finite():
        return "—"
    if not number:
        return "0" + suffix
    places = max(2, 2 - number.adjusted()) if abs(number) < Decimal("0.005") else 2
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits), number.adjusted() + places + 2)
        rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return format(rounded, ",f").rstrip("0").rstrip(".") + suffix


def _text_items(items: Iterable[Any]) -> list[Any]:
    """文本条目归一化：字符串当单条，空值丢弃。"""
    return as_text_items(items)


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
    for index, section in enumerate(as_dicts(sections), 1):
        sid = str(section.get("id") or f"s{index}")
        title = clean_title(section.get("title") or f"第 {index} 节")
        label = section.get("short") or (title if len(title) <= 14 else title[:14] + "…")
        links.append(f'<a href="#{esc(sid)}">{index:02d} {esc(label)}</a>')
    if len(links) < 2:
        return ""
    return f'<nav class="toc">{"".join(links)}</nav>'


def narrative(items: Any) -> str:
    """解读段落。"""
    blocks = [f"<p>{md_inline(item)}</p>" for item in as_text_items(items)]
    return f'<div class="narrative">{"".join(blocks)}</div>' if blocks else ""


def sql_details(sql: Any, label: str, method: str = "") -> str:
    """可折叠 SQL。"""
    sql_html = (
        f'<details class="sql"><summary>SQL · {esc(label)}</summary>'
        f"<pre>{esc(sql)}</pre></details>"
    ) if sql else ""
    return sql_html


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
    columns = [str(column) for column in as_items(table.get("columns"))]
    rows = as_items(table.get("rows"))
    if not columns and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    if not columns:
        return ""
    try:
        max_rows = max(1, int(table.get("max_rows") or 12))
    except (TypeError, ValueError):
        max_rows = 12
    shown = rows[:max_rows]
    body = []
    for row in shown:
        if isinstance(row, dict):
            cells = [row.get(column) for column in columns]
        elif isinstance(row, (list, tuple)):
            cells = row
        else:
            cells = [row]
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
            hypothesis = item.get("hypothesis") or item.get("causal_language") == "driver_hypothesis"
            alt_checked = as_text_items(item.get("alternative_checked"))
        else:
            text, hypothesis, alt_checked = item, False, []
        if not text:
            continue
        content = md_inline(text)
        if hypothesis:
            content += ' <span class="tag">假设</span>'
        if alt_checked:
            joined = "；".join(str(value) for value in alt_checked)
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
        rows = "".join(f"<li>{md_inline(text)}</li>" for text in as_text_items(item.get("items")))
        cols.append(
            '<div class="compare-col">'
            f'<span class="col-label">{esc(item.get("label") or "")}</span>'
            f'<span class="big">{fmt_value(item.get("value"))}'
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
        f'<strong>{fmt_value(data.get("value"))}</strong></div>'
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
        value = item.get("value")
        display = (
            md_inline(value) if isinstance(value, str) and not _DECIMAL_VALUE.fullmatch(value.strip())
            else fmt_value(value)
        )
        cards.append(
            '<div class="driver">'
            f'<span class="share">{fmt_value(item.get("share"))}</span>'
            f'<strong>{md_inline(item.get("label") or "")}</strong>'
            f'<p>{display}</p>{tag}</div>'
        )
    return f'<section class="drivers">{"".join(cards)}</section>' if cards else ""


def alternatives(section: dict[str, Any]) -> str:
    """归因块的替代解释检验。"""
    items = _text_items(section.get("alternatives"))
    if not items:
        items = []
        for raw in as_text_items(section.get("alternative_checked")):
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
