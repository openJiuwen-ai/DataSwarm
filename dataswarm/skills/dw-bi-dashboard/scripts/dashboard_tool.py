#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dashboard_tool.py —— BI 看板管理工具（dw-bi-dashboard skill）。

方案批次 2：HTTP 调用统一经 `dwcli dashboard *`（web API），本脚本不再自建
urllib 客户端（§3.2 skill 侧禁止自建 HTTP）。

用法:
    python dashboard_tool.py import-report --spec-path X [--dashboard-name Y]
    python dashboard_tool.py create-dashboard --name N [--description D]
    python dashboard_tool.py list-dashboards
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _dwcli(*args: str) -> dict:
    """subprocess 调 dwcli；返回 JSON；业务失败时返回带 error 的 dict。"""
    cmd = [sys.executable or "python", "-m", "jiuwenswarm.cli.dwcli", *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)  # noqa: S603
    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        payload = {"error": f"dwcli 输出非 JSON: {proc.stdout[:300]!r}"}
    if proc.returncode != 0 and "error" not in payload:
        payload["error"] = f"dwcli exit {proc.returncode}"
    return payload


def _tmp_json(payload) -> str:
    import tempfile
    fd = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8", prefix="dwcli_body_")
    json.dump(payload, fd, ensure_ascii=False, default=str)
    fd.close()
    return fd.name


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """兼容原 _request 签名：method+path+body → dwcli dashboard 子命令。

    映射（method+path → dwcli group action）：
        POST /import-from-report → dashboard import-from-report
        POST /dashboards         → dashboard dashboards-create
        GET  /dashboards         → dashboard dashboards
    """
    if method == "POST" and path == "/import-from-report":
        jf = _tmp_json(body or {})
        return _dwcli("dashboard", "import-from-report", "--json-file", jf)
    if method == "POST" and path == "/dashboards":
        jf = _tmp_json(body or {})
        return _dwcli("dashboard", "dashboards-create", "--json-file", jf)
    if method == "GET" and path == "/dashboards":
        return _dwcli("dashboard", "dashboards")
    raise ValueError(f"dashboard_tool 不支持的请求: {method} {path}")


# 仅用于拼接展示链接；端口与 dwcli 默认 base 对齐（DWAGENT_BASE 可覆盖，其次 WEB_API_PORT）。
# 兜底取 release 单实例 16173（app_web 静态 + /api 一体）；dev 源码形态由注入的 WEB_API_PORT 覆盖
_env_api_port = next(
    (str(raw) for raw in (os.environ.get("WEB_API_PORT"), os.environ.get("JIUWENSWARM_WEB_API_PORT"))
     if str(raw or "").isdigit()),
    "16173")
API_BASE = (os.environ.get("DWAGENT_BASE") or f"http://127.0.0.1:{_env_api_port}") + "/api/dataagent/dashboard"


def cmd_import_report(args) -> None:
    """从 dw-report 产出导入看板。"""
    spec_path = str(Path(args.spec_path).expanduser().resolve())
    body = {
        "spec_path": spec_path,
    }
    if args.dashboard_name:
        body["dashboard_name"] = args.dashboard_name
    if args.datasource_ref:
        body["datasource_ref"] = args.datasource_ref

    result = _request("POST", "/import-from-report", body)
    if "error" in result:
        print(f"导入失败: {result['error']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"导入成功! 看板ID: {result.get('dashboard_id')}")
        print(f"看板名称: {result.get('name')}")
        cards = result.get("cards", [])
        print(f"卡片数: {len(cards)}")
        for c in cards:
            print(f"  - {c.get('title', 'untitled')} ({c.get('card_type', 'unknown')})")
        rel = result.get("dashboard_url")
        if not rel and result.get("dashboard_id"):
            rel = f"/dataagent/dashboard/dashboards?dashboard_id={result['dashboard_id']}"
        if rel:
            print(f"看板链接: {rel}")


def cmd_create_dashboard(args) -> None:
    """新建看板。"""
    body = {"name": args.name}
    if args.description:
        body["description"] = args.description
    result = _request("POST", "/dashboards", body)
    if "error" in result:
        print(f"创建失败: {result['error']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"创建成功! 看板ID: {result.get('dashboard_id')}")


def cmd_list_dashboards(args) -> None:
    """列出看板。"""
    result = _request("GET", "/dashboards")
    dashboards = result.get("dashboards", [])
    if not dashboards:
        print("暂无看板")
        return
    for d in dashboards:
        print(f"  {d['dashboard_id']}  {d['name']}  ({d.get('cards_count', 0)} 卡片)  {d['status']}")



def main():
    parser = argparse.ArgumentParser(description="BI看板管理工具")
    sub = parser.add_subparsers(dest="action")

    # import-report
    p_import = sub.add_parser("import-report", help="从 dw-report 产出导入看板")
    p_import.add_argument("--spec-path", required=True, help="spec JSON 文件路径")
    p_import.add_argument("--dashboard-name", help="看板名称（默认取 spec.title）")
    p_import.add_argument("--datasource-ref", help="数据源引用")

    # create-dashboard
    p_cd = sub.add_parser("create-dashboard", help="新建看板")
    p_cd.add_argument("--name", required=True, help="看板名称")
    p_cd.add_argument("--description", help="看板描述")

    # list-dashboards
    sub.add_parser("list-dashboards", help="列出看板")

    args = parser.parse_args()

    if args.action == "import-report":
        cmd_import_report(args)
    elif args.action == "create-dashboard":
        cmd_create_dashboard(args)
    elif args.action == "list-dashboards":
        cmd_list_dashboards(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
