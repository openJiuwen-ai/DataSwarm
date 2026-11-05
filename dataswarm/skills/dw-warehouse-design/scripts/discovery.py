# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单次探查快照 —— 替代旧流程 4~5 次独立探查调用。

一次返回：方言 + 业务库 + CSV schema（本地推断）+ 已有表（复用标记）。
方言必读（dwcli datasource current，本地解析）；已有表 best-effort（服务不可用时降级，不阻断设计）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import infer

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_MAX_COLUMNS_IN_OUTPUT = 50


def dwcli(*args: str) -> dict:
    """调 dwcli 子命令，返回 dict；失败时含 ok=False + error。"""
    cmd = [sys.executable, "-m", "jiuwenswarm.cli.dwcli"] + list(args)
    result = subprocess.run(cmd, capture_output=True)
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    if result.returncode != 0 and not stdout.strip():
        return {"ok": False, "error": stderr.strip() or f"dwcli 退出码 {result.returncode}"}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"dwcli 输出非 JSON: {stdout[:200]}", "raw": stdout}
    if isinstance(data, dict) and (data.get("status") == "error" or data.get("error")):
        return {"ok": False, "error": data.get("error") or json.dumps(data, ensure_ascii=False)}
    if isinstance(data, dict):
        data.setdefault("ok", True)
    return data


def _extract_table_names(payload) -> list[str]:
    """从 dwcli databases tables 返回中提取表名（兼容 list / {tables:[...]} / {data:[...]} 形态）。"""
    if payload is None:
        return []
    items = payload
    if isinstance(payload, dict):
        items = payload.get("tables") or payload.get("data") or payload.get("rows") or []
    if isinstance(items, dict):
        items = items.get("tables") or []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("table_name") or item.get("name") or item.get("table") or ""
            if name:
                names.append(str(name))
    return names


def _compact_file_info(info: dict) -> dict:
    columns = info["columns"]
    out = {k: v for k, v in info.items() if k != "columns"}
    out["columns"] = columns[:_MAX_COLUMNS_IN_OUTPUT]
    if len(columns) > _MAX_COLUMNS_IN_OUTPUT:
        out["columns_truncated"] = True
        out["columns_total"] = len(columns)
    return out


def snapshot(csv_dir: str | Path, files: list[str] | None = None,
             sample_rows: int = 200, compact: bool = True) -> dict:
    """一次快照：方言 + CSV schema + 已有表 + 复用标记。"""
    current = dwcli("datasource", "current")
    if not current.get("ok"):
        return {"ok": False, "step": "datasource current", "error": current.get("error", "unknown")}
    biz_database = str(current.get("biz_database") or "")
    if not biz_database:
        return {
            "ok": False, "step": "datasource current",
            "error": "biz_database 为空：请检查 ~/.dataswarm/config/datasource.yaml 的 default_db 配置",
        }

    try:
        file_infos = infer.infer_dir(csv_dir, files, sample_rows)
    except RuntimeError as exc:
        return {"ok": False, "step": "csv infer", "error": str(exc)}
    if not file_infos:
        return {"ok": False, "step": "csv infer", "error": f"目录中没有 CSV 文件: {csv_dir}"}

    tables_resp = dwcli("databases", "tables", "--db", biz_database)
    existing: list[str] = []
    note = ""
    if tables_resp.get("ok"):
        existing = _extract_table_names(tables_resp)
    else:
        note = f"已有表查询不可用（{tables_resp.get('error', '')}），复用标记已降级为全部新建"

    existing_lower = {str(n).lower() for n in existing}
    reuse: list[dict] = []
    create: list[dict] = []
    for info in file_infos:
        short = f"ods_{infer.sanitize_stem(str(info['stem']))}"
        entry = {
            "file": info["file"],
            "ods_table": short,
            "row_count": info["row_count"],
            "columns": len(info["columns"]),
        }
        (reuse if short.lower() in existing_lower else create).append(entry)

    return {
        "ok": True,
        "dialect": current.get("dialect", "doris"),
        "biz_database": biz_database,
        "default_db": current.get("default_db", ""),
        "csv_dir": str(csv_dir),
        "csv_files": [_compact_file_info(i) for i in file_infos] if compact else file_infos,
        "existing_tables_count": len(existing),
        "reuse": reuse,
        "create": create,
        "greenfield": len(existing) == 0,
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="数仓建设单次探查快照")
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--files", default="", help="逗号分隔的文件名（可选）")
    parser.add_argument("--sample-rows", type=int, default=200)
    args = parser.parse_args()
    files = [f.strip() for f in args.files.split(",") if f.strip()] or None
    print(json.dumps(snapshot(args.csv_dir, files, args.sample_rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
