"""产物门禁：自包含、图表实例、体积、结构完整。"""
from __future__ import annotations

import json
import re
from pathlib import Path

MAX_BYTES = 6 * 1024 * 1024
_CHARTS = re.compile(r"var CHARTS = (\[.*?\]);\nvar instances", re.DOTALL)


def verify_html(path: Path, min_charts: int = 1) -> list[str]:
    """检查 HTML 产物，返回错误列表。"""
    errors: list[str] = []
    if not path.is_file():
        return [f"文件不存在: {path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    size = path.stat().st_size
    if size > MAX_BYTES:
        errors.append(f"体积 {size} 超过上限 {MAX_BYTES}")
    if not text.lstrip().lower().startswith("<!doctype html"):
        errors.append("缺少 <!DOCTYPE html>")
    if "</html>" not in text.lower():
        errors.append("缺少 </html>")
    for pattern in ('src="http', "src='http", 'href="http', "href='http"):
        if pattern in text.lower():
            errors.append(f"存在外链资源: {pattern}")
    match = _CHARTS.search(text)
    if not match:
        if min_charts > 0:
            errors.append("未找到图表初始化脚本（var CHARTS）")
    else:
        try:
            charts = json.loads(match.group(1))
        except ValueError as exc:
            errors.append(f"图表数据不是合法 JSON: {exc}")
            charts = []
        if len(charts) < min_charts:
            errors.append(f"图表数量 {len(charts)} 少于要求 {min_charts}")
        for chart in charts:
            if not isinstance(chart, dict) or not chart.get("option"):
                errors.append("存在空图表 option")
                break
    if min_charts > 0 and "echarts.init" not in text:
        errors.append("缺少 echarts.init 调用")
    return errors
