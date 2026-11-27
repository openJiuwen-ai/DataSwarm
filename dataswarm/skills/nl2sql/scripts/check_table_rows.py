# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import emit, classify_error, emit_error


def check_table_rows(tables: list[str]) -> dict:
    from _db_client import count_rows

    checked = []
    for table in tables:
        result = count_rows(table)
        count = result["rows"][0][0] if result["rows"] else 0
        checked.append({"table": table, "row_count": count, "data_scope": result.get("data_scope")})
        if count == 0:
            return {"ok": False, "empty_table": table, "tables": checked}
    return {"ok": True, "tables": checked}


def main():
    parser = argparse.ArgumentParser(description="Per-table row count check (COUNT(*)=0 -> ok=false)")
    parser.add_argument("--tables", required=True, help="Comma-separated table names (e.g. bridge tables)")
    args = parser.parse_args()
    tbls = [t.strip() for t in args.tables.split(",") if t.strip()]
    try:
        emit(check_table_rows(tbls))
    except (RuntimeError, ValueError, OSError) as exc:
        kind, detail, _retry = classify_error(exc)
        emit_error(kind, detail)


if __name__ == "__main__":
    main()
