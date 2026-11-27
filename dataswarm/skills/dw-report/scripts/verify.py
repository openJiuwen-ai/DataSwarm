"""产物门禁：自包含、图表实例、体积、结构完整。"""

from __future__ import annotations

import json
import re
import os
import hashlib
from html.parser import HTMLParser
from pathlib import Path

from resolver import resolve
import dw_paths

MAX_BYTES = 6 * 1024 * 1024
_CHARTS = re.compile(r"var CHARTS = (\[.*?\]);\nvar instances", re.DOTALL)


class _ReportTypeParser(HTMLParser):
    """Read the standalone declaration from the document head, not report prose."""

    def __init__(self):
        super().__init__()
        self.in_head = False
        self.report_types: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "meta" and self.in_head:
            attributes = dict(attrs)
            if attributes.get("name") == "dw-report-type":
                self.report_types.append(attributes.get("content") or "")

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def verify_html(path: Path, min_charts: int = 0) -> list[str]:
    """检查 HTML 产物，返回错误列表。"""
    errors: list[str] = []
    if not path.is_file():
        return [f"文件不存在: {path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    gate_match = re.search(r'<script id="analysis-evidence-gate" type="application/json">(.*?)</script>', text, re.S)
    context = _ReportTypeParser()
    context.feed(text)
    standalone = context.report_types == ["standalone"]
    if context.report_types and not standalone:
        errors.append("报告类型声明无效或重复")
    if standalone and gate_match:
        errors.append("独立报告不能包含分析证据门禁记录")
    sid = os.environ.get("JIUWENSWARM_SESSION_ID", "").strip()
    if sid and not gate_match and not standalone:
        root = Path(os.environ.get("JIUWENSWARM_ARTIFACT_DIR") or dw_paths.artifact_root() / "analysis").expanduser()
        if (root / "by-session" / (hashlib.sha256(sid.encode()).hexdigest() + ".json")).exists():
            errors.append("本会话分析报告缺少证据门禁记录")
    if gate_match:
        try:
            payload = json.loads(gate_match.group(1))
            if not (payload.get("report_gate") or {}).get("ok"):
                errors.append("分析证据门禁未通过")
            manifest_path = Path(payload["manifest_path"])
            spec_path = manifest_path.parent / "report_spec.json"
            if (
                not payload.get("spec_hash")
                or hashlib.sha256(spec_path.read_bytes()).hexdigest() != payload["spec_hash"]
            ):
                raise ValueError("报告 spec 已变更，请重新渲染")
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            # Re-run artifact integrity and evidence checks, not only HTML QA.
            resolved = resolve(spec, strict=True, persist=False)
            min_charts = max(min_charts, resolved.get("_min_charts", 0))
        except Exception as exc:
            errors.append(f"分析证据无法复验: {exc}")
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
