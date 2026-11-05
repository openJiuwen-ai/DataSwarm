# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import argparse
import json
import os
import sys

# 固定标准输出编码，不跟系统 locale（中文 Windows 是 cp936）走，见 _db_client.py
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_client import query, emit, classify_error, failure  # noqa: E402


def explain_sql(sql: str) -> dict:
    try:
        result = query(f"EXPLAIN {sql}", limit=1000)
        plan = "\n".join(str(r[0]) for r in result["rows"] if r)
        return {"ok": True, "plan": plan, "estimated_rows": "unknown"}
    except Exception as exc:
        kind, why, retry = classify_error(exc)
        # 阶段归因（方言收敛批次 2-G7）：本脚本错误发生在执行计划预检
        return failure(kind, why, stage="SQL_EXPLAIN", retry=retry)


def main():
    parser = argparse.ArgumentParser(description="SQL explain plan precheck")
    parser.add_argument("--sql", required=True)
    args = parser.parse_args()
    result = explain_sql(args.sql)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=True, default=str), file=sys.stderr)
        sys.exit(1)
    emit(result)


if __name__ == "__main__":
    main()
