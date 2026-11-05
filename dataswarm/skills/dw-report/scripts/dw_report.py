"""dw-report CLI：validate / render / verify。

用法：
    python dw_report.py validate --spec report_spec.json
    python dw_report.py render --spec report_spec.json [--data data.json] [--db db-ref]
                               [--html report.html] [--md report.md] [--strict]
    python dw_report.py verify --html report.html [--min-charts 1]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from inline_assets import inline_echarts  # noqa: E402
from plan_schema import validate as validate_spec  # noqa: E402
from render.markdown import render_markdown  # noqa: E402
from render.page import detect_page, render_html  # noqa: E402
from resolver import load_spec, resolve  # noqa: E402
from verify import verify_html  # noqa: E402

SKILL_DIR = HERE.parent
ASSETS = SKILL_DIR / "assets"


def _analysis_dir() -> Path:
    base = os.environ.get("JIUWENSWARM_DATA_DIR") or str(Path.home() / ".dataswarm")
    return Path(base) / "artifact" / "analysis"


def _check_output(value: str | None, suffix: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.suffix.lower() != suffix:
        raise SystemExit(f"[dw-report] 输出路径扩展名必须是 {suffix}: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _summary(spec: dict, charts: dict, page: str) -> str:
    sections = len(spec.get("sections") or [])
    return f"{spec.get('title') or ''} | {page} | {sections} sections | {len(charts)} charts"


def cmd_validate(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    errors = validate_spec(spec)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "page": detect_page(spec)}, ensure_ascii=False))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    errors = validate_spec(spec)
    if errors:
        print("[dw-report] spec 校验失败：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_dir = _analysis_dir()
    html_path = _check_output(args.html, ".html") or (
        (default_dir / f"report_{stamp}.html") if not args.md else None
    )
    md_path = _check_output(args.md, ".md") or (
        (default_dir / f"report_{stamp}.md") if not args.html else None
    )
    if html_path is None and md_path is None:
        print("[dw-report] 至少需要一个输出载体（--html / --md）", file=sys.stderr)
        return 1

    try:
        resolved = resolve(spec, data_path=args.data, db=args.db, strict=args.strict)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[dw-report] {exc}", file=sys.stderr)
        return 1
    charts = resolved.get("_charts") or {}
    warnings = resolved.get("_warnings") or []

    formats = []
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html = render_html(resolved, charts, ASSETS)
        html_path.write_text(html, encoding="utf-8")
        code = inline_echarts(html_path, ASSETS / "echarts.min.js")
        if code != 0:
            return code
        gate = verify_html(html_path, min_charts=1 if charts else 0)
        if gate:
            print("[dw-report] 产物门禁失败：", file=sys.stderr)
            for error in gate:
                print(f"  - {error}", file=sys.stderr)
            return 1
        formats.append("html")

    if md_path is not None:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(resolved), encoding="utf-8")
        formats.append("md")

    result = {
        "mode": "path",
        "page": detect_page(resolved),
        "formats": formats,
        "html_path": str(html_path) if html_path else None,
        "md_path": str(md_path) if md_path else None,
        "size_bytes": html_path.stat().st_size if html_path else 0,
        "summary": _summary(resolved, charts, detect_page(resolved)),
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    errors = verify_html(Path(args.html).expanduser(), min_charts=args.min_charts)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "html": args.html}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dw_report", description="dw-report 报告渲染器")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验 spec")
    p_validate.add_argument("--spec", required=True)
    p_validate.set_defaults(func=cmd_validate)

    p_render = sub.add_parser("render", help="渲染 html / md")
    p_render.add_argument("--spec", required=True)
    p_render.add_argument("--data", help="data.json（inline 图表 / 表格）")
    p_render.add_argument("--db", help="db-ref（sql 段取数）")
    p_render.add_argument("--html", help="HTML 输出路径")
    p_render.add_argument("--md", help="Markdown 输出路径")
    p_render.add_argument("--strict", action="store_true", help="证据缺失/哈希不符时报错")
    p_render.set_defaults(func=cmd_render)

    p_verify = sub.add_parser("verify", help="校验 HTML 产物")
    p_verify.add_argument("--html", required=True)
    p_verify.add_argument("--min-charts", type=int, default=0)
    p_verify.set_defaults(func=cmd_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
