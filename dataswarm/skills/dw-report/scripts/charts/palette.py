"""图表色板与旧产物重着色。"""
from __future__ import annotations

import re
from typing import Any

# 页面 token 同源：主蓝、正向绿、警示黄、负向红、辅助紫/青
PALETTE = ["#2F6BFF", "#1E8E5A", "#B7791F", "#C6423F", "#7A5AF8", "#0E9F9F"]

# 历史 ECharts option 色值 → 新色板
_OLD = {
    "#4E7CF6": "#2F6BFF", "#4e7cf6": "#2F6BFF",
    "#22C08D": "#1E8E5A", "#22c08d": "#1E8E5A",
    "#F59E0B": "#B7791F", "#f59e0b": "#B7791F",
    "#F0574E": "#C6423F", "#f0574e": "#C6423F",
    "#ECEEF2": "#ECEFF3", "#eceef2": "#ECEFF3",
    "#D9DEE7": "#E6E8EC", "#d9dee7": "#E6E8EC",
    "#D9DEE7FF": "#E6E8EC",
}

_RGBA = re.compile(
    r"rgba\(\s*78\s*,\s*124\s*,\s*246\s*,", re.IGNORECASE
)


def retheme(value: Any) -> Any:
    """递归替换旧产物中的色值，不动数据结构。

    Args:
        value: 任意 ECharts option 片段（dict / list / str / 标量）。

    Returns:
        同结构的新对象；字符串中的旧色值替换为新色板。
    """
    if isinstance(value, str):
        text = _OLD.get(value, value)
        return _RGBA.sub("rgba(47, 107, 255,", text)
    if isinstance(value, list):
        return [retheme(item) for item in value]
    if isinstance(value, dict):
        return {key: retheme(item) for key, item in value.items()}
    return value
