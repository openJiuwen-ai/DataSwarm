# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw-warehouse-build/scripts/execute_pipeline.py — 任务化执行编排脚本。

编排执行流程：读方案 → 建任务/工作流 → publish → 执行。
委托 plan_to_tasks.py 一次性完成建任务+工作流+节点+publish+血缘+执行。

用法：
    python execute_pipeline.py --action read-plan --plan-id <id>
    python execute_pipeline.py --action run --plan-id <id> [--db <db_path>] [--no-run]
    python execute_pipeline.py --action append --plan-id <id> --table-spec-file <json_file> [--db <db_path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 强制 stdout/stderr 走 UTF-8，避免 Windows 默认 GBK 在输出含 \ufffd 等字符时抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        pass

# 定位本 skill 的 plan_to_tasks.py
_PLAN_TO_TASKS = Path(__file__).resolve().parent / "plan_to_tasks.py"


def _dwcli(*args, workspace_id: str = "") -> dict:
    """调 dwcli 子命令；workspace_id 非空时显式透传（多工作空间下必传）。"""
    cmd = [sys.executable, "-m", "jiuwenswarm.cli.dwcli"] + list(args)
    if workspace_id:
        cmd += ["--workspace-id", workspace_id]
    result = subprocess.run(cmd, capture_output=True)
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "raw": stdout}


def action_read_plan(args) -> None:
    """读方案。"""
    r = _dwcli("plan", "get", "--plan-id", args.plan_id,
               workspace_id=getattr(args, "workspace_id", ""))
    _emit({"ok": True, "action": "read_plan", **r})


def _subprocess_env(args) -> dict:
    """构造子进程 env：--db → JIUWENSWARM_ORCH_DB，--workspace-id →
    JIUWENSWARM_WORKSPACE_ID（plan_to_tasks 无这两个参数，走 env 通道）。"""
    env = os.environ.copy()
    if getattr(args, "db", ""):
        env["JIUWENSWARM_ORCH_DB"] = args.db
    if getattr(args, "workspace_id", ""):
        env["JIUWENSWARM_WORKSPACE_ID"] = args.workspace_id
    return env


def _get_biz_database(plan_id: str, workspace_id: str = "") -> str:
    """通过 dwcli plan get 读取方案的 biz_database（多工作空间需传 workspace_id）。

    plan 索引行含 biz_database 字段；旧索引行可能只存了 biz_domain（=business），
    兜底从 tables[0].table_name（形如 "olist.dwd_xxx"）取库名前缀。
    """
    r = _dwcli("plan", "get", "--plan-id", plan_id, workspace_id=workspace_id)
    if not isinstance(r, dict):
        return ""
    biz = r.get("biz_database") or r.get("business") or r.get("biz_domain") or ""
    if biz:
        return biz
    tables = r.get("tables") or []
    if tables:
        tn = tables[0].get("table_name") or ""
        if "." in tn:
            return tn.split(".", 1)[0]
    return ""


def _build_links(biz_database: str) -> dict:
    """构造数据管理平台 Markdown 链接。"""
    if not biz_database:
        return {}
    return {
        "tables": f"[📊 库表管理](/data-management/tables?db={biz_database})",
    }


def _emit_subprocess_result(action: str, result: subprocess.CompletedProcess, stdout: str, stderr: str,
                            plan_id: str = "", workspace_id: str = "") -> None:
    """汇总 plan_to_tasks 的文本输出：按退出码判定 ok，从输出解析运行摘要。"""
    ok = result.returncode == 0
    summary: dict = {"action": action}
    if plan_id:
        summary["plan_id"] = plan_id
    m = re.search(r"\[instance\]\s+(\S+)\s+status=(\S+)", stdout)
    if m:
        summary["instance_id"] = m.group(1)
        summary["instance_status"] = m.group(2)
    m = re.search(r"\[runs\]\s+success=(\d+)/(\d+)", stdout)
    if m:
        summary["runs_success"] = int(m.group(1))
        summary["runs_total"] = int(m.group(2))
    if ok and summary.get("instance_status") == "failed":
        ok = False
    if ok:
        links = _build_links(_get_biz_database(plan_id, workspace_id)) if plan_id else {}
        _emit({"ok": True, **summary, **({"links": links} if links else {})})
    else:
        _emit({"ok": False, **summary, "raw": stdout, "stderr": stderr.strip()})


def action_run(args) -> None:
    """全量执行：调 plan_to_tasks.py 建任务+工作流+执行。"""
    if not _PLAN_TO_TASKS.exists():
        _emit({"ok": False, "error": f"plan_to_tasks.py not found: {_PLAN_TO_TASKS}"})
        return
    cmd = [sys.executable, str(_PLAN_TO_TASKS), "--plan-id", args.plan_id]
    if args.no_run:
        cmd.append("--no-run")
    result = subprocess.run(cmd, capture_output=True, env=_subprocess_env(args))
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    _emit_subprocess_result("run", result, stdout, stderr, plan_id=args.plan_id,
                            workspace_id=getattr(args, "workspace_id", ""))


def action_append(args) -> None:
    """增量追加：调 plan_to_tasks.py --action append。"""
    if not _PLAN_TO_TASKS.exists():
        _emit({"ok": False, "error": f"plan_to_tasks.py not found: {_PLAN_TO_TASKS}"})
        return
    cmd = [sys.executable, str(_PLAN_TO_TASKS),
           "--plan-id", args.plan_id, "--action", "append"]
    if args.table_spec_file:
        cmd += ["--table-spec-file", args.table_spec_file]
    if args.table_spec:
        cmd += ["--table-spec", args.table_spec]
    result = subprocess.run(cmd, capture_output=True, env=_subprocess_env(args))
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    _emit_subprocess_result("append", result, stdout, stderr, plan_id=args.plan_id,
                            workspace_id=getattr(args, "workspace_id", ""))


def _emit(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="数仓任务化执行编排")
    parser.add_argument("--action", required=True,
                       choices=["read-plan", "run", "append"],
                       help="操作：read-plan=读方案, run=全量执行, append=增量追加")
    parser.add_argument("--plan-id", required=True, help="方案 ID")
    parser.add_argument("--db", default="", help="编排库路径（sqlite）")
    parser.add_argument("--workspace-id", default=os.environ.get("JIUWENSWARM_WORKSPACE_ID", ""),
                        help="工作空间 ID（多工作空间下读 plan 必传；缺省读 JIUWENSWARM_WORKSPACE_ID）")
    parser.add_argument("--no-run", action="store_true", help="只建+publish，不执行")
    parser.add_argument("--table-spec-file", default="", help="增量追加表规格文件")
    parser.add_argument("--table-spec", default="", help="增量追加表规格 JSON")
    args = parser.parse_args()

    if args.action == "read-plan":
        action_read_plan(args)
    elif args.action == "run":
        action_run(args)
    elif args.action == "append":
        action_append(args)


if __name__ == "__main__":
    main()
