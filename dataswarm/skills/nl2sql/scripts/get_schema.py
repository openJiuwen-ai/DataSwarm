# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import (emit, emit_error, classify_error, database_name, schema, failure, tables)


def _available_tables() -> list[str]:
    """列出当前源的全部表名；只用于报错提示，任何失败都返回空列表。"""
    try:
        names = tables().get("tables") or []
        if names:
            return names
    except Exception:
        pass
    return []


def _schema_duckdb(table: str) -> dict:
    """通过统一元数据接口读取字段，保留原生库的 schema、约束和注释。"""
    try:
        result = schema(table)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        if "dataset is not registered" not in str(exc):
            emit_error(et, msg)
        # 目录源（excel/csv 目录）每个文件一个视图，表名取文件名；
        # 传入目录名（如 exnw）时追加 available_tables，供调用方直接改用真实表名。
        payload = failure("TABLE_NOT_FOUND", f"table '{table}' not found; use one of the available table names")
        available = _available_tables()
        if available:
            payload["error"]["available_tables"] = available
            payload["error"]["hint"] = "传 --table <available_tables 中的表名>"
        print(json.dumps(payload, ensure_ascii=True), file=sys.stderr)
        sys.exit(1)
    columns = result.get("columns", [])
    if not columns:
        emit_error("TABLE_NOT_FOUND", f"{table} not found or has no columns")
    return {
        "database": database_name(),
        # 原生库按 schema 暴露（"schema"."table"）：裸表名解析后回填规范名，
        # 下游写 SQL 直接用它，避免默认 search_path 只有 main 时查不到。
        "table": result.get("resolved_table") or table,
        "columns": [
            {"name": c["name"], "type": c["type"], "comment": c.get("comment") or "",
             "nullable": c.get("nullable", c.get("null", True)), "ordinal": i + 1}
            for i, c in enumerate(columns)
        ],
    }


def get_schema(table: str, include_partitions: bool = False) -> dict:
    return _schema_duckdb(table)


def main():
    parser = argparse.ArgumentParser(description="On-demand full schema pull")
    parser.add_argument("--table", required=True)
    parser.add_argument("--include_partitions", action="store_true")
    args = parser.parse_args()
    emit(get_schema(args.table, args.include_partitions))


if __name__ == "__main__":
    main()
