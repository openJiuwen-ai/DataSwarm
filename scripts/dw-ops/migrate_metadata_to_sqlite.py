#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""migrate_metadata_to_sqlite.py —— 把系统元数据从 Doris 拷到 SQLite（逐工作空间）。

为什么需要它：
    元数据库部署在阿里云 SelectDB 公网端点，而应用在腾讯云，每次 API 调用要发
    多次跨云往返（实测 50~200ms/次；数据开发页首屏合计 30 次往返 ≈ 1.5~6s）。
    把元数据落到本地 SQLite 后往返降到亚毫秒。

方向与安全性：
    Doris(源) → SQLite(目标)。**源库全程只读**，原样保留作为备份与回滚点——
    改回 datasource.yaml 的 metadata_db 即可切回，无需重新导数据。
    目标库是「工作空间私有库」，命名沿用 workspace_store.create_workspace 的约定
    （``~/.jiuwenswarm/config/workspace_<workspace_id>.db``）。

    对每张表：先在目标库建齐 DDL（ensure_system_schema 幂等），再清空目标表重灌，
    因此重复执行结果一致（不会累加重复行）。

用法：
    python scripts/dw-ops/migrate_metadata_to_sqlite.py                    # 预演（默认，不写库）
    python scripts/dw-ops/migrate_metadata_to_sqlite.py --execute          # 实际执行
    python scripts/dw-ops/migrate_metadata_to_sqlite.py --execute --workspace default
    python scripts/dw-ops/migrate_metadata_to_sqlite.py --execute --target-dir /tmp/meta

注意：
    data source yaml 里的 default_db（业务库）不受本脚本影响，只有 metadata_db 被迁移。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jiuwenswarm.channels.web.schema.ensure import ensure_system_schema  # noqa: E402
from jiuwenswarm.common import db_connector as dc  # noqa: E402
from jiuwenswarm.common.utils import get_user_workspace_dir  # noqa: E402

# 数据根跟随 JIUWENSWARM_DATA_DIR（隔离形态与 db_connector 同一目录），无 env 回退 ~/.jiuwenswarm
_data_home = get_user_workspace_dir()
DEFAULT_CONFIG = _data_home / "config" / "datasource.yaml"
DEFAULT_TARGET_DIR = _data_home / "config"


def load_workspaces(config_path: Path) -> list[tuple[str, str]]:
    """从 datasource.yaml 读出 (workspace_id, metadata_db_ref) 列表。

    顶层 metadata_db = 默认工作空间（电商）；named workspaces 逐个追加。

    Args:
        config_path: datasource.yaml 路径

    Returns:
        [(workspace_id, db_ref), ...]，默认工作空间恒在首位

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置里没有任何 metadata_db
    """
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    out: list[tuple[str, str]] = []
    top_ref = str(data.get("metadata_db") or "").strip()
    if top_ref:
        out.append(("default", top_ref))
    for wid, spec in (data.get("workspaces") or {}).items():
        ref = str((spec or {}).get("metadata_db") or "").strip()
        if ref:
            out.append((str(wid), ref))
    if not out:
        raise ValueError(f"{config_path} 里没有任何 metadata_db 配置")
    return out


def _norm_cell(value):
    """把驱动返回的 bytes 解成 str（Doris 二进制 charset 列会回 bytes）。"""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _copy_table(src_ref: str, dst_ref: str, schema_ref: str, table: str, execute: bool) -> dict:
    """单表 Doris → SQLite。返回 {table, rows, cols, missing_cols, elapsed_ms}。

    schema_ref 是「列结构参照库」——正式执行时就是 dst_ref，预演时是临时库。
    只拷两侧都存在的列（交集），源库多出的列记在 missing_cols 里报告出来，
    不静默丢——那通常意味着 DDL 与线上库漂移了。
    """
    src_cols = [c["name"] for c in dc.schema(src_ref, table)["columns"]]
    dst_cols = [c["name"] for c in dc.schema(schema_ref, table)["columns"]]
    cols = [c for c in src_cols if c in dst_cols]
    missing = [c for c in src_cols if c not in dst_cols]
    if not cols:
        return {"table": table, "rows": 0, "cols": 0, "missing_cols": missing, "elapsed_ms": 0}

    started = time.time()
    col_sql = ", ".join(f"`{c}`" for c in cols)
    result = dc.query(src_ref, f"SELECT {col_sql} FROM `{table}`", limit=None)
    rows = [tuple(_norm_cell(v) for v in row) for row in result["rows"]]

    if execute:
        dc.execute(dst_ref, f"DELETE FROM `{table}`")
        placeholders = ", ".join("?" for _ in cols)
        dc.executemany(
            dst_ref,
            f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})",
            rows,
        )
    return {
        "table": table,
        "rows": len(rows),
        "cols": len(cols),
        "missing_cols": missing,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def migrate_workspace(wid: str, src_ref: str, dst_ref: str, execute: bool,
                      scratch_dir: Path) -> dict:
    """迁移单个工作空间的全部共享表。返回汇总 dict。

    预演模式不碰真实目标库，但**仍要在临时库里建一份同样的 schema**——否则目标列
    集合为空，交集为空，会把所有列误报成「列漂移」、行数恒为 0，预演就失去意义。
    """
    print(f"\n{'=' * 78}")
    print(f"[工作空间 {wid}]")
    print(f"  源(只读): {src_ref}")
    print(f"  目标    : {dst_ref}")
    if execute:
        ensure_system_schema(dst_ref)
        schema_ref = dst_ref
        print("  建表中  : ensure_system_schema -> ok")
    else:
        schema_ref = f"sqlite://{scratch_dir / f'preview_{wid}.db'}"
        ensure_system_schema(schema_ref)
        print(f"  建表中  : [预演] schema 建在临时库 {schema_ref}")

    src_tables = set(dc.tables(src_ref)["tables"])
    dst_tables = set(dc.tables(schema_ref)["tables"])
    shared = sorted(src_tables & dst_tables)
    only_src = sorted(src_tables - dst_tables)

    print(f"  源表 {len(src_tables)} 张，目标表 {len(dst_tables)} 张，共享 {len(shared)} 张")
    if only_src:
        print(f"  [警告] 源库有 {len(only_src)} 张表目标库没有（DDL 缺）：{', '.join(only_src)}")

    total_rows = 0
    details: list[dict] = []
    for table in shared:
        try:
            info = _copy_table(src_ref, dst_ref, schema_ref, table, execute)
        except Exception as exc:  # noqa: BLE001 —— 单表失败不掩盖，报告后继续
            print(f"    [失败] {table}: {type(exc).__name__}: {str(exc)[:120]}")
            details.append({"table": table, "error": str(exc)[:200]})
            continue
        total_rows += info["rows"]
        details.append(info)
        warn = ""
        if info["missing_cols"]:
            warn = f"  [列漂移] 源多出未拷：{', '.join(info['missing_cols'])}"
        print(f"    {table:<28} {info['rows']:>6} 行  {info['elapsed_ms']:>5} ms{warn}")

    print(f"  合计 {total_rows} 行")
    return {"workspace": wid, "src": src_ref, "dst": dst_ref,
            "tables": len(shared), "rows": total_rows, "details": details}


def main() -> int:
    ap = argparse.ArgumentParser(description="把元数据从 Doris 迁到 SQLite（逐工作空间）")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help=f"datasource.yaml 路径（默认 {DEFAULT_CONFIG}）")
    ap.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR,
                    help=f"SQLite 库落盘目录（默认 {DEFAULT_TARGET_DIR}）")
    ap.add_argument("--workspace", action="append", default=None,
                    help="只处理指定工作空间（可重复）；缺省处理全部")
    ap.add_argument("--execute", action="store_true",
                    help="实际写入；缺省为预演（只读源库、不建目标库）")
    args = ap.parse_args()

    workspaces = load_workspaces(args.config)
    if args.workspace:
        wanted = set(args.workspace)
        workspaces = [(w, r) for w, r in workspaces if w in wanted]
        if not workspaces:
            print(f"[ERROR] --workspace {sorted(wanted)} 在配置里不存在", file=sys.stderr)
            return 1

    print(f"配置     : {args.config}")
    print(f"目标目录 : {args.target_dir}")
    print(f"模式     : {'执行' if args.execute else '预演（不写任何库）'}")
    print("迁移目标 : " + ", ".join(f"{w}" for w, _ in workspaces))

    if args.execute:
        args.target_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    # 预演用的临时目录手动管理：Windows 下连接池仍持有 sqlite 文件句柄，
    # TemporaryDirectory 的自动清理会抛 PermissionError 掩盖真正的结果。
    scratch_dir = Path(tempfile.mkdtemp(prefix="meta-sqlite-preview-"))
    try:
        for wid, src_ref in workspaces:
            dst_path = args.target_dir / f"workspace_{wid}.db"
            dst_ref = f"sqlite://{dst_path}"
            summaries.append(
                migrate_workspace(wid, src_ref, dst_ref, args.execute, scratch_dir)
            )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    print(f"\n{'=' * 78}")
    print("汇总")
    for s in summaries:
        print(f"  {s['workspace']:<10} {s['tables']:>3} 表  {s['rows']:>7} 行  ->  {s['dst']}")
    if not args.execute:
        print("\n[预演结束] 未写任何库。确认无误后加 --execute 重跑。")
    else:
        print("\n[完成] 别忘了把 datasource.yaml 的 metadata_db 改指向上述 sqlite 路径，再重启服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
