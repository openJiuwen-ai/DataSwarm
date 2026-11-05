"""把 ECharts 资产内联进 HTML，产物保持单文件自包含。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SRC = re.compile(r'<script[^>]*src=["\']__ECHARTS__["\'][^>]*>\s*</script>', re.IGNORECASE)
_COMMENT = re.compile(r"<!--\s*__ECHARTS_INLINE__\s*-->")


def inline_echarts(html_path: Path, asset_path: Path) -> int:
    """把 ECharts 内联进 HTML，覆盖写回。"""
    if not html_path.is_file():
        print(f"[dw-report] 找不到 HTML: {html_path}", file=sys.stderr)
        return 1
    if not asset_path.is_file():
        print(f"[dw-report] 找不到 ECharts 资产: {asset_path}", file=sys.stderr)
        return 1
    html = html_path.read_text(encoding="utf-8")
    asset = asset_path.read_text(encoding="utf-8")
    tag = f"<script>\n{asset}\n</script>"
    new_html, replaced = _SRC.subn(lambda _m: tag, html)
    if not replaced:
        new_html, replaced = _COMMENT.subn(lambda _m: tag, html)
    if not replaced:
        print(
            "[dw-report] 未找到 ECharts 占位符 "
            '(<script src="__ECHARTS__"></script> 或 <!--__ECHARTS_INLINE__-->)',
            file=sys.stderr,
        )
        return 1
    html_path.write_text(new_html, encoding="utf-8")
    return 0
