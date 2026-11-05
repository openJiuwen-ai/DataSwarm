"""DataFrame → ECharts option。

入口是 :func:`chart_option` + :class:`ChartRequest`：请求对象显式描述
「图型 / 维度 / 度量 / 拆分列」，不依赖位置参数顺序。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from charts.palette import PALETTE, retheme

_FRIENDLY = {
    "ck_sales_amount": "销售额",
    "ck_invoice_count": "发票数",
    "ck_aov": "客单价",
    "gmv": "GMV",
    "orders": "订单数",
    "orders_cnt": "订单数",
    "revenue": "收入",
    "amount": "金额",
    "count": "数量",
}


@dataclass(frozen=True)
class ChartRequest:
    """图表请求。

    Attributes:
        kind: ``line`` / ``bar`` / ``pie`` / ``scatter`` / ``heatmap``。
        category: 横轴或分类列名。
        measures: 数值列名；多列时生成多序列。
        split_by: 可选分组列；单度量时按该列拆序列。
        palette: 可选色板。
    """

    kind: str
    category: str
    measures: tuple[str, ...]
    split_by: str | None = None
    palette: tuple[str, ...] | None = None

    @classmethod
    def from_mapping(cls, config: dict[str, Any]) -> "ChartRequest":
        """从 ``{"type", "x", "y", "color"}`` 配置构造请求。"""
        raw = config.get("y")
        if isinstance(raw, (list, tuple)):
            measures = tuple(str(item) for item in raw)
        elif raw is None:
            measures = ()
        else:
            measures = (str(raw),)
        return cls(
            kind=str(config.get("type") or "bar").lower(),
            category=str(config.get("x") or ""),
            measures=measures,
            split_by=config.get("color"),
        )


def json_safe(value: Any) -> Any:
    """把 numpy / pandas / datetime 递归转成 JSON 可序列化对象。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    return value


def friendly_series_name(value: Any) -> str:
    """把原始列名转成图例可读名。"""
    name = str(value or "").strip()
    if not name:
        return "数值"
    if name in _FRIENDLY:
        return _FRIENDLY[name]
    if re.search(r"[一-鿿]", name):
        return name
    if re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return name.replace("_", " ").title()
    return name


def normalize_option(option: dict[str, Any]) -> dict[str, Any]:
    """把完整 ECharts option 归一化：重着色、可读图例名、单序列去图例。"""
    option = retheme(option)
    target = option.get("baseOption") if isinstance(option.get("baseOption"), dict) else option
    series = target.get("series")
    if isinstance(series, list):
        for item in series:
            if isinstance(item, dict):
                item["name"] = friendly_series_name(item.get("name"))
        if len(series) == 1:
            target.pop("legend", None)
    return option


def chart_option(frame: pd.DataFrame, request: ChartRequest) -> dict[str, Any]:
    """按请求从 DataFrame 构造 ECharts option。

    Args:
        frame: 数据表。
        request: 图表请求。

    Returns:
        ECharts option dict。

    Raises:
        ValueError: 列缺失或图表类型不支持。
    """
    if frame is None or frame.empty:
        return {
            "color": list(request.palette or PALETTE),
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": []},
            "yAxis": {"type": "value"},
            "series": [],
        }
    cleaned = _normalize_frame(frame)
    kind = request.kind.lower()
    category = request.category
    measures = [item for item in request.measures if item]
    colors = list(request.palette or PALETTE)
    _ensure_columns(cleaned, [category])

    if kind == "pie":
        return _pie_option(cleaned, category, measures, colors)
    if kind == "heatmap":
        return _heatmap_option(cleaned, category, measures, request.split_by, colors)

    if not measures:
        raise ValueError(f"{kind} 需要至少一个度量列")
    _ensure_columns(cleaned, [category, *measures])

    if request.split_by and len(measures) == 1:
        return _split_option(cleaned, request, colors)

    if kind == "scatter":
        return _scatter_option(cleaned, category, measures, colors)

    return _cartesian_option(cleaned, category, measures, kind, colors)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """datetime → ISO 字符串、NaN → None。"""
    output = frame.copy()
    for column in output.columns:
        if pd.api.types.is_datetime64_any_dtype(output[column]):
            output[column] = output[column].dt.strftime("%Y-%m-%d")
    return output.where(pd.notna(output), None)


def _column_values(series: pd.Series) -> list[Any]:
    return json_safe(series.tolist())


def _ensure_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"图表引用的列不存在: {missing}；CSV 实际列: {list(frame.columns)}"
        )


def _cartesian_base(colors: Sequence[str]) -> dict[str, Any]:
    return {
        "color": list(colors),
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 52, "right": 26, "top": 28, "bottom": 36, "containLabel": True},
        "xAxis": {
            "type": "category",
            "axisTick": {"show": False},
            "axisLine": {"lineStyle": {"color": "#E6E8EC"}},
            "axisLabel": {"color": "#8A94A6", "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#ECEFF3"}},
            "axisLabel": {"color": "#8A94A6", "fontSize": 10},
        },
    }


def _series_item(kind: str, name: str, data: list[Any], color: str) -> dict[str, Any]:
    if kind == "line":
        return {
            "name": name, "type": "line", "data": data,
            "smooth": True, "symbol": "circle", "symbolSize": 5,
            "lineStyle": {"width": 2.5, "color": color},
            "itemStyle": {"color": color},
            "areaStyle": {"opacity": 0.08},
            "labelLayout": {"hideOverlap": True},
        }
    if kind == "bar":
        return {"name": name, "type": "bar", "data": data,
                "itemStyle": {"color": color}}
    raise ValueError(f"不支持的图表类型: {kind}（支持 line / bar / pie / scatter / heatmap）")


def _cartesian_option(
    frame: pd.DataFrame,
    category: str,
    measures: list[str],
    kind: str,
    colors: Sequence[str],
) -> dict[str, Any]:
    series = [
        _series_item(kind, column, _column_values(frame[column]),
                     colors[index % len(colors)])
        for index, column in enumerate(measures)
    ]
    option = _cartesian_base(colors)
    option["xAxis"]["data"] = [str(value) for value in frame[category].tolist()]
    option["series"] = series
    if kind == "bar":
        for item in option["series"]:
            item["barMaxWidth"] = 28
            item["itemStyle"] = {"borderRadius": [4, 4, 0, 0]}
    if len(measures) > 1:
        option["legend"] = {"top": 0, "right": 0, "icon": "circle",
                            "textStyle": {"color": "#5A6270", "fontSize": 10}}
    return option


def _split_option(
    frame: pd.DataFrame, request: ChartRequest, colors: Sequence[str]
) -> dict[str, Any]:
    measure = request.measures[0]
    pivot = frame.pivot_table(
        index=request.category, columns=request.split_by, values=measure, aggfunc="sum"
    )
    option = _cartesian_base(colors)
    option["xAxis"]["data"] = [str(value) for value in pivot.index.tolist()]
    option["series"] = [
        _series_item(request.kind, str(column), _column_values(pivot[column]),
                     colors[index % len(colors)])
        for index, column in enumerate(pivot.columns)
    ]
    option["legend"] = {"top": 0, "right": 0, "icon": "circle",
                        "textStyle": {"color": "#5A6270", "fontSize": 10}}
    return option


def _scatter_option(
    frame: pd.DataFrame, category: str, measures: list[str], colors: Sequence[str]
) -> dict[str, Any]:
    column = measures[0]
    numeric = pd.api.types.is_numeric_dtype(frame[category])
    if numeric:
        data = [
            [json_safe(x), json_safe(y)]
            for x, y in zip(frame[category].tolist(), frame[column].tolist())
        ]
        return {
            "color": list(colors),
            "tooltip": {"trigger": "item"},
            "grid": {"left": 52, "right": 26, "top": 24, "bottom": 40, "containLabel": True},
            "xAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#ECEFF3"}},
                      "axisLabel": {"color": "#8A94A6", "fontSize": 10}},
            "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#ECEFF3"}},
                      "axisLabel": {"color": "#8A94A6", "fontSize": 10}},
            "series": [{"type": "scatter", "symbolSize": 7, "data": data}],
        }
    return _cartesian_option(frame, category, measures, "scatter", colors)


def _pie_option(
    frame: pd.DataFrame, category: str, measures: list[str], colors: Sequence[str]
) -> dict[str, Any]:
    if not measures:
        raise ValueError("pie 需要至少一个度量列")
    value_column = measures[0]
    _ensure_columns(frame, [category, value_column])
    return {
        "color": list(colors),
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
        "legend": {"bottom": 0, "icon": "circle",
                   "textStyle": {"color": "#5A6270", "fontSize": 10}},
        "series": [{
            "type": "pie",
            "radius": ["48%", "72%"],
            "center": ["50%", "44%"],
            "label": {"show": False},
            "itemStyle": {"borderColor": "#FFFFFF", "borderWidth": 2},
            "data": [
                {"name": str(name), "value": json_safe(value)}
                for name, value in zip(frame[category].tolist(), frame[value_column].tolist())
            ],
        }],
    }


def _heatmap_option(
    frame: pd.DataFrame,
    category: str,
    measures: list[str],
    split_by: str | None,
    colors: Sequence[str],
) -> dict[str, Any]:
    value_column = split_by or (measures[0] if measures else None)
    if not value_column:
        raise ValueError("heatmap 需要 color 指定数值列")
    y_column = measures[0] if measures and measures[0] != value_column else None
    if not y_column:
        raise ValueError("heatmap 需要 y 指定第二个分类列，color 指定数值列")
    _ensure_columns(frame, [category, y_column, value_column])
    x_labels = list(dict.fromkeys(str(value) for value in frame[category].tolist()))
    y_labels = list(dict.fromkeys(str(value) for value in frame[y_column].tolist()))
    data = [
        [x_labels.index(str(row[category])), y_labels.index(str(row[y_column])),
         json_safe(row[value_column])]
        for _, row in frame.iterrows()
    ]
    return {
        "tooltip": {"position": "top"},
        "grid": {"left": 60, "right": 30, "top": 20, "bottom": 60, "containLabel": True},
        "xAxis": {"type": "category", "data": x_labels,
                  "axisLabel": {"color": "#8A94A6", "fontSize": 10},
                  "axisTick": {"show": False}},
        "yAxis": {"type": "category", "data": y_labels,
                  "axisLabel": {"color": "#8A94A6", "fontSize": 10},
                  "axisTick": {"show": False}},
        "visualMap": {"min": min(value[2] for value in data) if data else 0,
                      "max": max(value[2] for value in data) if data else 1,
                      "calculable": True, "orient": "horizontal",
                      "left": "center", "bottom": 0,
                      "inRange": {"color": ["#EEF4FF", "#2F6BFF"]}},
        "series": [{"type": "heatmap", "data": data,
                    "label": {"show": True, "fontSize": 9}}],
    }
