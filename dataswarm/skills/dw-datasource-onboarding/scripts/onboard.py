#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""onboard.py —— 数据源接入脚本（dw-datasource-onboarding 主脚本）。

两个动作：
  detect    扫描本地目录/文件，识别数据类型（csv/excel/mixed/unknown）
  register  连通性测试（dwcli datasource test）→ 注册+自动绑定工作空间
            （dwcli datasource create，workspace_id 从会话 env 自动注入）

平台边界：元数据只经 dwcli 走 web API，本脚本不连任何数据库、不写元数据表。
spec 文件可能含凭据，读完即删。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_DW_COMMON_DIR = (Path(__file__).resolve().parent.parent.parent
                  / "_dw_common" / "scripts")
if str(_DW_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON_DIR))
import dw_client  # type: ignore  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ==========================================================================
# detect：本地路径类型识别
# ==========================================================================
def _suggest_source_id(detected_type: str, path: Path) -> str:
    base = path.stem if path.is_file() else path.name
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_").lower() or "data"
    prefix = detected_type if detected_type in ("csv", "excel") else "src"
    return f"{prefix}_{base}"[:64].rstrip("_-") or f"{prefix}_data"


def action_detect(path_str: str) -> dict:
    path = Path(path_str).expanduser()
    if not path.exists():
        return {"ok": False, "stage": "detect",
                "error": f"path not found on agent host: {path}",
                "hint": "路径必须是 Agent 运行机器的本地路径；用户本机数据需先上传/共享到服务器"}
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return {"ok": True, "kind": "file", "type": "csv",
                    "files": [path.name], "suggested_source_id": _suggest_source_id("csv", path)}
        if suffix in _EXCEL_SUFFIXES:
            return {"ok": True, "kind": "file", "type": "excel",
                    "files": [path.name], "suggested_source_id": _suggest_source_id("excel", path)}
        return {"ok": False, "stage": "detect", "type": "unknown",
                "error": f"unsupported file type: {suffix}",
                "hint": "支持 .csv / .xls / .xlsx / .xlsm；其他格式请向用户澄清"}

    csv_files = sorted(f.name for f in path.glob("*.csv"))
    excel_files = sorted(f.name for f in path.iterdir()
                         if f.is_file() and f.suffix.lower() in _EXCEL_SUFFIXES)
    if csv_files and not excel_files:
        return {"ok": True, "kind": "directory", "type": "csv",
                "files": csv_files, "suggested_source_id": _suggest_source_id("csv", path)}
    if excel_files and not csv_files:
        return {"ok": True, "kind": "directory", "type": "excel",
                "files": excel_files, "suggested_source_id": _suggest_source_id("excel", path)}
    if csv_files and excel_files:
        return {"ok": False, "stage": "detect", "type": "mixed",
                "error": "directory contains mixed CSV and Excel files",
                "detail": {"csv": csv_files, "excel": excel_files},
                "hint": "请向用户澄清要接入哪种/哪个文件，禁止擅自拆成多个数据源"}
    return {"ok": False, "stage": "detect", "type": "unknown",
            "error": f"no supported data files in directory: {path}",
            "hint": "目录中未发现 .csv/.xls/.xlsx/.xlsm；请向用户澄清数据格式"}


# ==========================================================================
# register：test → create（create 自带 workspace 绑定）
# ==========================================================================
def _dwcli_json(group: str, action: str, body: dict) -> dict:
    """dwcli json_body 命令封装：body 写临时文件（tmp 目录）→ --json-file 调用 → 删临时文件。"""
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".json", prefix="dwcli_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        return dw_client._dwcli(group, action, "--json-file", tmp_name)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def action_register(spec: dict) -> dict:
    source_type = str(spec.get("type", "")).strip()
    source_id = str(spec.get("source_id", "")).strip()
    params = dict(spec.get("params") or {})
    rebind = bool(spec.get("rebind"))
    if not source_type or not source_id or not params:
        return {"ok": False, "stage": "register",
                "error": "spec requires type, source_id, params"}
    if not _SOURCE_ID_RE.match(source_id):
        return {"ok": False, "stage": "register",
                "error": "source_id must be 1-64 chars of letters/digits/_/-"}

    workspace_id = dw_client.resolve_workspace_id()
    if not workspace_id:
        return {"ok": False, "stage": "register", "error_code": "NO_WORKSPACE_CONTEXT",
                "error": "会话未关联数据工作空间",
                "hint": "请先在前端创建或选择数据工作空间，再执行数据接入"}

    test_result = _dwcli_json("datasource", "test", {"type": source_type, **params})
    if test_result.get("ok") is False:
        return {"ok": False, "stage": "test", "error": test_result.get("error", "connection test failed")}

    create_body = {"source_id": source_id, "type": source_type, **params}
    description = str(spec.get("description") or "").strip()
    if description:
        create_body["description"] = description
    if rebind:
        create_body["rebind"] = True
    create_result = _dwcli_json("datasource", "create", create_body)
    return {"ok": True, "stage": "register", "test": test_result, "create": create_result,
            "source_id": create_result.get("source_id", source_id),
            "bound": bool(create_result.get("bound")),
            "workspace_id": create_result.get("workspace_id", workspace_id),
            "db_ref": create_result.get("db_ref", "")}


def main() -> None:
    parser = argparse.ArgumentParser(prog="onboard.py",
                                     description="数据源接入：类型识别 + 注册绑定工作空间")
    parser.add_argument("--action", required=True, choices=["detect", "register"])
    parser.add_argument("--path", default=None, help="detect：本地目录或文件路径")
    parser.add_argument("--spec-file", default=None,
                        help="register：spec JSON 文件路径（或 - 读 stdin）")
    parser.add_argument("--delete-spec", action="store_true",
                        help="register：读取后删除专用临时 spec（须显式指定）")
    args = parser.parse_args()

    if args.action == "detect":
        if not args.path:
            _emit({"ok": False, "error": "detect requires --path"})
            sys.exit(1)
        _emit(action_detect(args.path))
        return

    if not args.spec_file:
        _emit({"ok": False, "error": "register requires --spec-file"})
        sys.exit(1)
    spec_path: Path | None = None
    delete_spec = False
    if args.spec_file != "-":
        spec_path = Path(args.spec_file).expanduser()
    try:
        if spec_path is not None:
            # Credential-bearing specs are disposable only when they are regular
            # JSON files.  Never unlink a directory, device, symlink, or an
            # arbitrary non-JSON path merely because it was supplied here.
            if spec_path.suffix.lower() != ".json" or spec_path.is_symlink() or not spec_path.is_file():
                raise OSError("--spec-file must be an existing regular .json file (symlinks are not allowed)")
            delete_spec = args.delete_spec
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        else:
            spec = json.loads(sys.stdin.read())
    except (OSError, json.JSONDecodeError) as exc:
        _emit({"ok": False, "error": f"读取 spec 失败: {exc}"})
        sys.exit(1)
    finally:
        if delete_spec and spec_path is not None:
            try:
                spec_path.unlink()  # 已验证的凭据 JSON，读完即删
            except FileNotFoundError:
                pass
    result = action_register(spec)
    _emit(result)
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
