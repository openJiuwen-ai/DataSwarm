# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import csv
import json
import os
import sys
import time

# 固定标准输出编码，不跟系统 locale（中文 Windows 是 cp936）走，见 _db_client.py
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import query, emit, classify_error, failure  # noqa: E402


HEAD_ROWS = 10  # export.head 携带的行数（不含表头）


def execute_sql(sql: str) -> dict:
    try:
        result = query(sql, limit=100000)
        columns = [
            {"name": c, "type": "", "comment": ""}
            for c in result["columns"]
        ]
        rows = result["rows"]

        ts = int(time.time())
        csv_path = (f"/tmp/query_result_{ts}.csv" if os.name != "nt"
                    else os.path.join(os.environ.get("TEMP", "/tmp"), f"query_result_{ts}.csv"))
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([c["name"] for c in columns])
            for row in rows:
                writer.writerow(row)

        head = [[str(v) if v is not None else "" for v in row] for row in rows[:HEAD_ROWS]]
        return {
            "ok": True,
            "sql": sql,
            "row_count": len(rows),
            "columns": columns,
            "export": {"file": csv_path, "head": head},
        }
    except Exception as exc:
        kind, why, retry = classify_error(exc)
        # 阶段归因（方言收敛批次 2-G7）：本脚本错误发生在真执行
        return failure(kind, why, stage="SQL_EXECUTION", retry=retry)


def main():
    parser = argparse.ArgumentParser(description="SQL execution + CSV export")
    parser.add_argument("--sql", required=True)
    args = parser.parse_args()
    result = execute_sql(args.sql)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=True, default=str), file=sys.stderr)
        sys.exit(1)
    emit(result)


if __name__ == "__main__":
    main()
