# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import emit, schema, classify_error, emit_error


def sample_dim_values(table: str, columns: list[str]) -> dict:
    """Only sample the columns requested by the caller, through the common SQL builder."""
    from _db_client import sample_values

    metadata = schema(table)
    selected = [column for column in metadata["columns"] if column["name"] in columns]
    if not selected:
        return {"table": table, "mode": "distinct", "dim_columns": [], "count": 0}
    result = sample_values(table, [column["name"] for column in selected])
    return {"table": metadata["resolved_table"], "mode": "distinct", "count": len(selected),
            "data_scope": result.get("data_scope"), "truncated": result.get("truncated", False),
            "dim_columns": [{"name": column["name"], "type": column["type"],
                             "comment": column.get("comment", ""),
                             "values": list(dict.fromkeys(str(row[index]) for row in result["rows"]
                                                          if row[index] is not None))[:20]}
                            for index, column in enumerate(selected)]}


def main():
    parser = argparse.ArgumentParser(description="Adaptive dimension value recall")
    parser.add_argument("--table", required=True)
    parser.add_argument("--columns", required=True, help="Comma-separated column names")
    args = parser.parse_args()
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    try:
        emit(sample_dim_values(args.table, cols))
    except (RuntimeError, ValueError, OSError) as exc:
        kind, detail, _retry = classify_error(exc)
        emit_error(kind, detail)


if __name__ == "__main__":
    main()
