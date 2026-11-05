#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw_error_classify.py —— 跨 skill 错误分类纯函数（唯一真相源）。

从 nl2sql/_db_client.py classify_error 提升而来（方言收敛批次 2-G7，
轻量版）。nl2sql 保留薄委托，其他 skill
（dw-task-execute / execute_sql 等）按需 import，不再各自裸抛异常文本。

error_phase 三值（输出统一阶段归因，帮 Agent 判断失败发生在哪一层）：
  - SQL_VALIDATION：生成期静态校验（render / plan_to_tasks 校验器）
  - SQL_DRY_RUN：执行前预检（EXPLAIN / LIMIT 0 包装）
  - SQL_EXECUTION：真执行报错（本文件的分类目标）
"""
from __future__ import annotations

import re

# SQL 执行错误分类（error_phase=SQL_EXECUTION）
_TABLE_NOT_FOUND_RE = re.compile(
    r"Catalog Error: Table with name .+ does not exist|"
    r"Catalog Error: Table with name .+ not found"
)
_COLUMN_NOT_FOUND_RE = re.compile(r"Catalog Error: Column with name .+ does not exist")
# 裸表名在多个 schema 下同名：不是拼写错误，也不是服务故障——改写全限定名即可。
_AMBIGUOUS_TABLE_RE = re.compile(r"is ambiguous across schemas", re.IGNORECASE)
_SYNTAX_RE = re.compile(
    r"errCode\s*=\s*2|mismatched input|Parse error|Syntax error|syntax error"
    r"|\(1064,|\(1105,|ParserException|Binder Error|Catalog Error",
    re.IGNORECASE,
)


def classify_error(exc: Exception) -> tuple[str, str, bool]:
    """按错误文本分类异常 → (error_type, message, retryable)。

    MySQL 错误码走 ``(code,`` 前缀匹配（pymysql 文本形态）；DuckDB 走
    ParserException / Binder Error / Catalog Error 文本（无错误码）。
    Catalog Error 表/列分支先于通用 not found——列不存在要改 SELECT，
    表不存在要换表，修复动作不同。
    """
    msg = str(exc)

    def _has_code(*codes: int) -> bool:
        return any(f"({c}," in msg for c in codes)

    if _has_code(1142, 1143) or "Access denied" in msg:
        return "PERMISSION_DENIED", msg, False
    if _has_code(3024, 20101) or "Query timeout" in msg:
        return "TIMEOUT", msg, True
    if _AMBIGUOUS_TABLE_RE.search(msg):
        return "AMBIGUOUS_TABLE", msg, True
    if _TABLE_NOT_FOUND_RE.search(msg):
        return "TABLE_NOT_FOUND", msg, False
    if _COLUMN_NOT_FOUND_RE.search(msg):
        return "COLUMN_NOT_FOUND", msg, False
    if _has_code(1146, 1051) or "doesn't exist" in msg or "not found" in msg.lower():
        return "TABLE_NOT_FOUND", msg, False
    if _SYNTAX_RE.search(msg):
        return "SYNTAX_ERROR", msg, True
    return "UNKNOWN", msg, True


# error_type → 修复建议（与 nl2sql SUGGESTIONS 同口径，消费方可覆盖）
SUGGESTIONS: dict[str, str] = {
    "PERMISSION_DENIED": "联系 DBA 申请该表的 select 权限",
    "TABLE_NOT_FOUND": "确认表名拼写或检查 information_schema.tables",
    "AMBIGUOUS_TABLE": '表名在多个 schema 下同名，改用全限定名（如 "schema"."table"）',
    "COLUMN_NOT_FOUND": "确认列名拼写，用 get_schema.py 重新拉取表结构",
    "SYNTAX_ERROR": "检查 SQL 语法，参考 SQL 方言注意事项",
    "TIMEOUT": "添加 LIMIT 或收紧 WHERE 条件后重试",
    "UNKNOWN": "查看数据库日志或重试",
}


def with_phase(error_type: str, phase: str = "SQL_EXECUTION") -> dict:
    """错误分类结果 → 带 error_phase 的 dict（统一输出形态）。"""
    return {
        "error_type": error_type,
        "error_phase": phase,
        "suggestion": SUGGESTIONS.get(error_type, ""),
    }
