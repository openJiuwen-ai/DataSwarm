# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import (query, emit, emit_error, classify_error, quote_ident,
                        database_name, source_type, schema)


def _schema_duckdb(table: str) -> dict:
    """通过统一元数据接口读取字段，保留原生库的 schema、约束和注释。"""
    try:
        result = schema(table)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)
    columns = result.get("columns", [])
    if not columns:
        emit_error("TABLE_NOT_FOUND", f"{table} not found or has no columns")
    return {
        "database": database_name(),
        "table": table,
        "columns": [
            {"name": c["name"], "type": c["type"], "comment": c.get("comment") or "",
             "nullable": c.get("nullable", c.get("null", True)), "ordinal": i + 1}
            for i, c in enumerate(columns)
        ],
    }


def get_schema(table: str, include_partitions: bool = False) -> dict:
    if source_type() == "duckdb":
        return _schema_duckdb(table)
    db = database_name()
    if not db:
        emit_error("MISSING_CONFIG",
                   "db-ref 未含 database schema，information_schema 查询需要库名")
    # information_schema 禁占位符（Nereids arity bug），且 _db_client.query 无
    # params 通道——一律字面拼接（字符串字面量转义）。
    col_sql = (
        "SELECT column_name, column_type, column_comment, is_nullable, ordinal_position "
        "FROM information_schema.columns "
        f"WHERE table_schema = '{db.replace(chr(39), chr(39) * 2)}' "
        f"AND table_name = '{table.replace(chr(39), chr(39) * 2)}' "
        "ORDER BY ordinal_position"
    )
    try:
        result = query(col_sql)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)

    col_rows = result["rows"]
    if not col_rows:
        emit_error("TABLE_NOT_FOUND", f"{db}.{table} not found or has no columns")

    out = {
        "database": db,
        "table": table,
        "columns": [
            {
                "name": r[0],
                "type": r[1],
                "comment": r[2] or "",
                "nullable": r[3] == "YES",
                "ordinal": r[4],
            }
            for r in col_rows
        ],
    }

    if include_partitions:
        part_sql = f"SHOW PARTITIONS FROM {quote_ident(db)}.{quote_ident(table)} LIMIT 100"
        try:
            part_result = query(part_sql, limit=100)
        except Exception as exc:
            et, msg, _ = classify_error(exc)
            emit_error(et, msg)
        out["partitions"] = [{"name": r[0]} for r in part_result["rows"]]
    return out


def main():
    parser = argparse.ArgumentParser(description="On-demand full schema pull")
    parser.add_argument("--table", required=True)
    parser.add_argument("--include_partitions", action="store_true")
    args = parser.parse_args()
    emit(get_schema(args.table, args.include_partitions))


if __name__ == "__main__":
    main()
