#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ensure_workspace_meta.py —— 逐工作空间把元数据库对齐到当前代码的 schema 形态。

为什么需要它：
    ensure_system_schema 只建「缺失表」（CREATE TABLE IF NOT EXISTS），**不给存量表加列**；
    各模块的加列函数（BI 指标语义层、巡检评审/归属列、血缘 task_id/business_ref）平时靠
    「首次用到该模块」惰性触发，批量升级时逐个触发不可控、易漏（漏了就是运行期才炸）。
    本脚本一次性遍历所有工作空间执行，全部幂等。

每个工作空间执行：
    1. ensure_system_schema —— 按 deploy/schema DDL 建缺失表
    2. 按 DDL 补列（通用机制）—— 逐 DDL 文件解析声明列，缺哪列按 DDL 类型 ALTER 补上
       （这是主机制：DDL 是列结构唯一真相源，新加列不需要在脚本里再登记一次）
    3. 模块接口兜底 —— metrics.ensure_semantic_columns / inspection_service
       .ensure_review_columns / .ensure_runs_columns（覆盖 DDL 之外的历史列）

用法（部署目录下，用部署 venv 跑）：
    /opt/dataswarm/.venv/bin/python scripts/dw-ops/ensure_workspace_meta.py
    ... --schema-src /opt/code/dataswarm/deploy/schema   # 指定 DDL 真相源（默认本仓库）
    ... --verify                                         # 逐 DDL 校验列漂移，只报告不改动
    ... --workspace bank                                 # 只处理指定工作空间（可重复）
    ... --dry-run                                        # 只列工作空间与目标库，不执行
    ... --skip-runtime-sync                              # 不刷新运行目录 DDL 副本
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_SRC = REPO_ROOT / "deploy" / "schema"


def _runtime_schema_dst() -> Path:
    """Return this instance's runtime schema directory."""
    configured = os.environ.get("JIUWENSWARM_DATA_DIR", "").strip()
    data_dir = Path(configured).expanduser() if configured else Path.home() / ".jiuwenswarm"
    return data_dir / "deploy-schema"

_COLUMN_RE = re.compile(r"^`([^`]+)`\s+\S")
_TYPE_RE = re.compile(r"^`([^`]+)`\s+([A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?)")


def _ddl_columns_typed(ddl_text: str) -> list[tuple[str, str]]:
    """从 CREATE TABLE DDL 解析 (列名, 类型) 列表（类型供 ALTER ADD COLUMN 复用）。

    Args:
        ddl_text: 单条 CREATE TABLE 语句文本（已去注释行）。

    Returns:
        [(列名, 类型字符串), ...]，按 DDL 顺序；类型不含 NULL/COMMENT 等修饰。

    Raises:
        ValueError: 找不到列定义括号块。
    """
    body = _ddl_column_block(ddl_text)
    out: list[tuple[str, str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _TYPE_RE.match(stripped)
        if match:
            out.append((match.group(1), " ".join(match.group(2).split())))
    return out


def _ddl_column_block(ddl_text: str) -> str:
    """取 CREATE TABLE 的列定义块（首个括号到其配对的右括号之间）。

    Args:
        ddl_text: 单条 CREATE TABLE 语句文本（已去注释）。

    Returns:
        列定义块文本。

    Raises:
        ValueError: 括号不配对（DDL 形态异常）。
    """
    start = ddl_text.index("(")
    depth = 0
    for i in range(start, len(ddl_text)):
        ch = ddl_text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return ddl_text[start + 1:i]
    raise ValueError("DDL 未找到配对的列定义块")


def _ddl_columns(ddl_text: str) -> list[str]:
    """从 CREATE TABLE DDL 解析声明列名（跳过注释行与约束行）。

    Args:
        ddl_text: 单条 CREATE TABLE 语句文本（已去注释）。

    Returns:
        声明列名列表，按 DDL 顺序。
    """
    cols = []
    for line in _ddl_column_block(ddl_text).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _COLUMN_RE.match(stripped)
        if match:
            cols.append(match.group(1))
    return cols


def _verify_workspace(meta_ref: str, schema_dir: Path, dialect: str) -> tuple[list[str], int]:
    """逐 DDL 文件比对声明列与实际库列，返回 (漂移明细, 检查表数)。

    Args:
        meta_ref: 目标元数据库 db-ref。
        schema_dir: DDL 目录（含 ddl/ 子目录）。
        dialect: 方言（sqlite/doris）。

    Returns:
        (漂移明细行列表, 实际检查的表数)。空列表代表无列级漂移。
    """
    from jiuwenswarm.common import db_connector as dc

    drifts: list[str] = []
    # 只比对建表 DDL：sqlite 侧另有 CREATE INDEX 语句文件（如 idx_bi_*），
    # 它们没有列定义，按建表解析会误报「表不存在」。
    files = [f for f in sorted((schema_dir / "ddl").glob(f"*.{dialect}.sql"))
             if "CREATE TABLE" in f.read_text(encoding="utf-8").upper()]
    for f in files:
        table = f.name.split(".")[0]
        text = "\n".join(
            ln for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        )
        declared = _ddl_columns(text)
        try:
            live = {c.get("name") for c in dc.schema(meta_ref, table).get("columns", [])}
        except Exception as exc:  # noqa: BLE001 —— 表不存在/探测失败都算漂移
            drifts.append(f"{table}: 探测失败（表可能不存在）— {exc}")
            continue
        if not live:
            drifts.append(f"{table}: 库中不存在该表（{len(declared)} 列未落地）")
            continue
        missing = [c for c in declared if c not in live]
        if missing:
            drifts.append(f"{table}: 缺 {len(missing)} 列 → {', '.join(missing)}")
    return drifts, len(files)


def _sync_runtime_schema(schema_src: Path, destination: Path | None = None) -> int:
    """把 DDL 真相源复制到运行目录副本（ensure 的解析优先级里运行目录在前）。

    Args:
        schema_src: 源 DDL 目录，须含 ddl/ 子目录。

    Returns:
        ddl 目录下的 .sql 文件数。

    Raises:
        RuntimeError: 源目录缺少 ddl/ 子目录。
    """
    if not (schema_src / "ddl").is_dir():
        raise RuntimeError(f"DDL 源目录缺少 ddl/: {schema_src}")
    destination = destination or _runtime_schema_dst()
    shutil.copytree(schema_src, destination, dirs_exist_ok=True)
    return len(list((destination / "ddl").glob("*.sql")))


def _add_missing_columns(meta_ref: str, schema_dir: Path, dialect: str) -> tuple[list[str], list[str]]:
    """逐 DDL 文件把声明列的缺失项 ALTER 补上（通用机制，不依赖各模块惰性补列）。

    DDL 是列结构的唯一真相源：CREATE TABLE IF NOT EXISTS 只建缺失表，存量表加列
    必须走 ALTER；此处按 DDL 声明类型补齐，避免「新加一列就得记得在升级脚本里
    加一个调用」的漏改模式。新列一律以 NULL 追加（NOT NULL 无默认值无法对存量行生效）。

    Args:
        meta_ref: 目标元数据库 db-ref。
        schema_dir: DDL 目录（含 ddl/ 子目录）。
        dialect: 方言（sqlite/doris），决定读哪套 DDL。

    Returns:
        (成功补列明细 ["表.列", ...], 失败明细 ["表.列: 原因", ...])。
    """
    from jiuwenswarm.common import db_connector as dc

    added: list[str] = []
    failed: list[str] = []
    # 同 --verify：跳过 CREATE INDEX 等非建表 DDL（无列定义，建表由 ensure 负责）
    for f in (f for f in sorted((schema_dir / "ddl").glob(f"*.{dialect}.sql"))
              if "CREATE TABLE" in f.read_text(encoding="utf-8").upper()):
        table = f.name.split(".")[0]
        text = "\n".join(
            ln for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        )
        declared = _ddl_columns_typed(text)
        try:
            live = {c.get("name") for c in dc.schema(meta_ref, table).get("columns", [])}
        except Exception:  # noqa: BLE001 —— 表不存在时由建表步骤负责，这里跳过
            continue
        if not live:
            continue
        for name, col_type in declared:
            if name in live:
                continue
            try:
                dc.execute(meta_ref, f"ALTER TABLE `{table}` ADD COLUMN `{name}` {col_type} NULL")
                added.append(f"{table}.{name}")
            except Exception as exc:  # noqa: BLE001 —— 单列失败不影响其余列
                failed.append(f"{table}.{name}: {exc}")
    return added, failed


def _ensure_one(meta_ref: str, schema_dir: Path, dialect: str) -> dict:
    """对单个元数据库执行建表 + 补列，返回各步结果（异常就地捕获，不中断遍历）。

    Args:
        meta_ref: 目标元数据库 db-ref。
        schema_dir: DDL 目录（含 ddl/ 子目录）。
        dialect: 方言（sqlite/doris）。

    Returns:
        {"tables": [...], "esteps": {"步骤名": 结果文本}} 形态的结果字典；
        任一步失败时结果文本为 "失败: <原因>"。
    """
    from jiuwenswarm.channels.web.schema.ensure import ensure_system_schema

    out: dict = {"tables": [], "already": 0, "esteps": {}}

    try:
        report = ensure_system_schema(meta_ref)
        out["tables"] = report.get("ensured_tables", [])
        out["already"] = report.get("already_present", 0)
        out["dialect"] = report.get("dialect", "")
        out["schema_dir"] = report.get("schema_dir", "")
    except Exception as exc:  # noqa: BLE001 —— 单步失败继续跑其余步骤，汇总上报
        out["esteps"]["建表 ensure_system_schema"] = f"失败: {exc}"

    try:
        added, failed = _add_missing_columns(meta_ref, schema_dir, dialect)
        out["added_columns"] = added
        if failed:
            out["esteps"]["按 DDL 补列"] = f"失败 {len(failed)} 列: " + "; ".join(failed)
        else:
            out["esteps"]["按 DDL 补列"] = f"补 {len(added)} 列" if added else "无缺失列"
    except Exception as exc:  # noqa: BLE001
        out["esteps"]["按 DDL 补列"] = f"失败: {exc}"

    try:
        from jiuwenswarm.channels.web.dataagent.metrics import db as metric_db
        metric_db.ensure_semantic_columns(meta_ref)
        out["esteps"]["dw_bi_metrics 语义层 6 列（模块接口兜底）"] = "已应用"
    except Exception as exc:  # noqa: BLE001
        out["esteps"]["dw_bi_metrics 语义层 6 列（模块接口兜底）"] = f"失败: {exc}"

    try:
        from jiuwenswarm.channels.web import inspection_service
        inspection_service.ensure_review_columns(meta_ref)
        # 该函数内部对加列失败只写 warning 不抛异常，此处无法区分，如实标注
        out["esteps"]["dw_inspection_findings 评审 3 列（模块接口兜底）"] = "已应用（内部失败仅告警）"
    except Exception as exc:  # noqa: BLE001
        out["esteps"]["dw_inspection_findings 评审 3 列（模块接口兜底）"] = f"失败: {exc}"

    try:
        from jiuwenswarm.channels.web import inspection_service
        ok = inspection_service.ensure_runs_columns(meta_ref)
        out["esteps"]["dw_inspection_runs.workspace_id（模块接口兜底）"] = (
            "已应用" if ok else "失败（见后端告警）"
        )
    except Exception as exc:  # noqa: BLE001
        out["esteps"]["dw_inspection_runs.workspace_id"] = f"失败: {exc}"

    return out


def main() -> int:
    """遍历所有工作空间执行元数据库升级，返回进程退出码（有失败则 1）。"""
    ap = argparse.ArgumentParser(description="逐工作空间更新元数据库 schema/字段")
    ap.add_argument("--schema-src", type=Path, default=DEFAULT_SCHEMA_SRC,
                    help=f"DDL 真相源目录（默认 {DEFAULT_SCHEMA_SRC}）")
    ap.add_argument("--workspace", action="append", default=[],
                    help="只处理指定 workspace_id（可重复传；默认全部）")
    ap.add_argument("--dry-run", action="store_true", help="只列工作空间与目标库，不执行")
    ap.add_argument("--verify", action="store_true",
                    help="逐 DDL 文件比对声明列与实际库列，只报告不执行")
    ap.add_argument("--skip-runtime-sync", action="store_true",
                    help="不刷新 ~/.jiuwenswarm/deploy-schema 副本")
    args = ap.parse_args()

    from jiuwenswarm.common import workspace_context

    if not args.skip_runtime_sync and not args.dry_run:
        runtime_schema_dst = _runtime_schema_dst()
        n = _sync_runtime_schema(args.schema_src, runtime_schema_dst)
        print(f"[schema] {args.schema_src} -> {runtime_schema_dst}（ddl {n} 个）")

    records = workspace_context.list_workspaces()
    wanted = {w.strip() for w in args.workspace if w.strip()}
    if wanted:
        records = [r for r in records if r.workspace_id in wanted]
        missing = wanted - {r.workspace_id for r in records}
        if missing:
            print(f"[ERROR] 未知 workspace_id: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    if not records:
        print("[ERROR] 未发现任何工作空间（检查 datasource.yaml / workspaces.db）", file=sys.stderr)
        return 1

    print(f"[workspaces] 共 {len(records)} 个:\n")
    for rec in records:
        label = f"{rec.workspace_id} ({rec.name})"
        print(f"  {label:32s} metadata={workspace_context.sanitize_ref(rec.metadata_db_ref)}")
    print()

    if args.dry_run:
        print("[dry-run] 未执行任何变更。")
        return 0

    if args.verify:
        from jiuwenswarm.common import db_connector as _dc
        from jiuwenswarm.channels.web.schema.ensure import resolve_schema_dir

        schema_dir = args.schema_src if (args.schema_src / "ddl").is_dir() else resolve_schema_dir()
        print(f"[verify] DDL 源: {schema_dir}\n")
        total_drift = 0
        for rec in records:
            dialect = _dc.parse_db_ref(rec.metadata_db_ref).get("type", "sqlite")
            drifts, checked = _verify_workspace(rec.metadata_db_ref, schema_dir, dialect)
            head = f"── {rec.workspace_id} ({rec.name}) [检查 {checked} 张表/{dialect}] ──"
            print(head)
            if drifts:
                total_drift += len(drifts)
                for line in drifts:
                    print(f"  ✗ {line}")
            else:
                print("  ✓ 无列级漂移")
        print()
        if total_drift:
            print(f"[WARN] 共 {total_drift} 处列级漂移（ensure_system_schema 只建缺失表，不会补列）。",
                  file=sys.stderr)
            return 1
        print("[ok] 所有工作空间的元数据库列结构与 DDL 一致。")
        return 0

    failures = 0
    for rec in records:
        from jiuwenswarm.common import db_connector as _dc

        dialect = _dc.parse_db_ref(rec.metadata_db_ref).get("type", "sqlite")
        print(f"── 工作空间 {rec.workspace_id} ({rec.name}) ──")
        result = _ensure_one(rec.metadata_db_ref, args.schema_src, dialect)
        tables = result["tables"]
        if tables:
            print(f"  建表: 新建 {len(tables)} 张 → {', '.join(tables)}")
        else:
            print(f"  建表: 无缺失（存量 {result.get('already', 0)} 张）")
        for step, text in result["esteps"].items():
            print(f"  {step}: {text}")
            if str(text).startswith("失败"):
                failures += 1
        print()

    if failures:
        print(f"[WARN] 有 {failures} 个步骤失败，见上方明细。", file=sys.stderr)
        return 1
    print("[ok] 全部工作空间元数据库已对齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
