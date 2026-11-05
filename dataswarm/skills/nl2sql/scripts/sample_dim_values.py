# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import (query, emit, emit_error, classify_error, quote_ident,
                        database_name, source_type, schema, quote_table)


DIM_KEYWORDS = ["类别", "类型", "状态", "地区", "渠道", "分类"]


def is_dim_column(col_type: str, col_comment: str) -> bool:
    t = (col_type or "").lower()
    is_string = t.startswith("varchar") or t.startswith("char") or t.startswith("string")
    if not is_string:
        return False
    comment = col_comment or ""
    return any(k in comment for k in DIM_KEYWORDS)


def _duckdb_is_dim(col_type: str) -> bool:
    """DuckDB 文件源无列注释，字符串类（VARCHAR/CHAR/TEXT/ENUM）一律视为维度候选。"""
    return (col_type or "").upper().startswith(("VARCHAR", "CHAR", "TEXT", "ENUM"))


def _sample_dim_values_duckdb(table: str, columns: list[str]) -> dict:
    try:
        result = schema(table)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)
    # 裸表名解析后的规范名（原生库 "schema"."table"）：采样 SQL 必须用它，
    # 否则 main 之外的 schema 在默认 search_path 下查不到。
    table = result.get("resolved_table") or table
    dim_cols = [
        (c["name"], c["type"], c.get("comment") or "")
        for c in result.get("columns", [])
        if c["name"] in columns and _duckdb_is_dim(c["type"])
    ]
    if not dim_cols:
        return {"table": table, "mode": "distinct", "dim_columns": [], "count": 0}
    dim_col_names = [c[0] for c in dim_cols]
    if len(dim_cols) <= 3:
        sql = (f'SELECT DISTINCT {",".join(quote_ident(c) for c in dim_col_names)} '
               f'FROM {quote_table(table)} LIMIT 20')
        mode = "distinct"
    else:
        sql = (f'SELECT {",".join(quote_ident(c) for c in dim_col_names)} '
               f'FROM {quote_table(table)} LIMIT 1000')
        mode = "sample"
    try:
        value_result = query(sql, limit=1000)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)
    grouped: dict[str, list[str]] = {c[0]: [] for c in dim_cols}
    for row in value_result.get("rows", []):
        for i, col_name in enumerate(dim_col_names):
            val = row[i] if i < len(row) else None
            if val is not None and str(val) not in grouped[col_name]:
                grouped[col_name].append(str(val))
    return {
        "table": table,
        "mode": mode,
        "dim_columns": [
            {"name": c[0], "type": c[1], "comment": c[2], "values": grouped[c[0]][:20]}
            for c in dim_cols
        ],
        "count": len(dim_cols),
    }


def build_dim_query(table: str, columns: list[str], db: str) -> str:
    cols = ",".join(quote_ident(c) for c in columns)
    tbl = f"{quote_ident(db)}.{quote_ident(table)}"
    if len(columns) <= 3:
        return f"SELECT DISTINCT {cols} FROM {tbl} LIMIT 20", "distinct"
    return f"SELECT {cols} FROM {tbl} LIMIT 1000", "sample"


def sample_dim_values(table: str, columns: list[str]) -> dict:
    if source_type() == "duckdb":
        return _sample_dim_values_duckdb(table, columns)
    db = database_name()
    if not db:
        emit_error("MISSING_CONFIG",
                   "db-ref 未含 database schema，information_schema 查询需要库名")
    # information_schema 禁占位符（Nereids arity bug），且 _db_client.query 无 params
    # 通道——一律字面拼接（标识符经 quote_ident / 字符串字面量转义）。
    col_list = ", ".join("'" + c.replace("'", "''") + "'" for c in columns)
    col_sql = (
        f"SELECT column_name, column_type, column_comment, is_nullable "
        f"FROM information_schema.columns "
        f"WHERE table_schema = '{db.replace(chr(39), chr(39) * 2)}' "
        f"  AND table_name = '{table.replace(chr(39), chr(39) * 2)}' "
        f"  AND column_name IN ({col_list}) "
        f"ORDER BY ordinal_position"
    )
    try:
        result = query(col_sql)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)

    col_rows = result["rows"]
    dim_cols = [
        (r[0], r[1], r[2] or "")
        for r in col_rows
        if is_dim_column(r[1], r[2])
    ]
    if not dim_cols:
        return {"table": table, "mode": "distinct", "dim_columns": [], "count": 0}

    dim_col_names = [c[0] for c in dim_cols]
    sql, mode = build_dim_query(table, dim_col_names, db)
    try:
        value_result = query(sql, limit=1000)
    except Exception as exc:
        et, msg, _ = classify_error(exc)
        emit_error(et, msg)
    value_rows = value_result["rows"]

    if mode == "distinct":
        grouped: dict[str, list[str]] = {c[0]: [] for c in dim_cols}
        for row in value_rows:
            for i, col_name in enumerate(dim_col_names):
                val = row[i]
                if val is not None and str(val) not in grouped[col_name]:
                    grouped[col_name].append(str(val))
        dim_columns = [
            {"name": c[0], "type": c[1], "comment": c[2], "values": grouped[c[0]][:20]}
            for c in dim_cols
        ]
    else:
        per_col: dict[str, list[str]] = {c[0]: [] for c in dim_cols}
        for row in value_rows:
            for i, col_name in enumerate(dim_col_names):
                val = row[i]
                if val is not None and str(val) not in per_col[col_name]:
                    per_col[col_name].append(str(val))
        dim_columns = [
            {"name": c[0], "type": c[1], "comment": c[2], "values": per_col[c[0]][:20]}
            for c in dim_cols
        ]

    return {"table": table, "mode": mode, "dim_columns": dim_columns, "count": len(dim_columns)}


def main():
    parser = argparse.ArgumentParser(description="Adaptive dimension value recall")
    parser.add_argument("--table", required=True)
    parser.add_argument("--columns", required=True, help="Comma-separated column names")
    args = parser.parse_args()
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    emit(sample_dim_values(args.table, cols))


if __name__ == "__main__":
    main()
