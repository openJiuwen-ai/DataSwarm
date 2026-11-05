# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import query, emit, emit_error, classify_error, quote_ident, database_name


def check_table_rows(tables: list[str]) -> dict:
    db = database_name()
    if not db:
        emit_error("MISSING_CONFIG",
                   "db-ref 未含 database schema，COUNT(*) 查询需要库名")
    checked = []
    for tbl in tables:
        sql = f"SELECT COUNT(*) FROM {quote_ident(db)}.{quote_ident(tbl)} LIMIT 1"
        try:
            result = query(sql, limit=1)
        except Exception as exc:
            et, msg, _ = classify_error(exc)
            emit_error(et, msg)
        rows = result["rows"]
        count = rows[0][0] if rows else 0
        if count == 0:
            return {"ok": False, "empty_table": tbl}
        checked.append({"table": tbl, "row_count": count})
    return {"ok": True, "tables": checked}


def main():
    parser = argparse.ArgumentParser(description="Per-table row count check (COUNT(*)=0 -> ok=false)")
    parser.add_argument("--tables", required=True, help="Comma-separated table names (e.g. bridge tables)")
    args = parser.parse_args()
    tbls = [t.strip() for t in args.tables.split(",") if t.strip()]
    emit(check_table_rows(tbls))


if __name__ == "__main__":
    main()
