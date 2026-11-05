#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw_aggregate_check.py —— GROUP BY 聚合输出静态校验（唯一真相源，批次 1b G8）。

背景：Doris Nereids 对「GROUP BY 查询里非聚合输出列」在执行期才报
"not in aggregate's output"——实例跑到该任务才失败且报错不带 SQL 上下文。
此前两份独立校验：dw-warehouse-design/render.py（TableSpec 级，设计期）、
dw-warehouse-build/plan_to_tasks.py（正则级，落任务前）——正则口径各自
维护。收口为本模块：基于**成品 ETL SQL 文本**的校验，两个 skill 共用。

纯函数、无连接能力（_dw_common 约定，见 dw_sql_utils.py）。
"""
from __future__ import annotations

import re

# 聚合函数判定：SUM/AVG/MAX/MIN/COUNT 直接调用，或 ROUND/COALESCE 包裹聚合
AGG_FN_RE = re.compile(
    r"\b(SUM|AVG|MAX|MIN|COUNT|ROUND\(\s*(SUM|AVG|MAX|MIN|COUNT)|"
    r"COALESCE\(\s*(SUM|AVG|MAX|MIN|COUNT))\b", re.IGNORECASE,
)


def _split_top_commas(s: str) -> list[str]:
    """按顶层逗号切分（忽略括号内逗号）。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _norm_expr(expr: str) -> str:
    """表达式归一化（去空白/统一小写/剥引号），用于集合成员比较。"""
    return re.sub(r"\s+", "", expr).strip("`\"").lower()


def _strip_qualifier(expr_norm: str) -> str:
    """剥掉限定前缀（s.g → g），用于 GROUP BY 项与输出列的限定差异比较。"""
    return expr_norm.rsplit(".", 1)[-1] if "." in expr_norm else expr_norm


def assert_group_by_output(etl_sql: str, table_name: str) -> None:
    """校验 INSERT...SELECT...GROUP BY：非聚合输出列必须在 GROUP BY 中。

    只处理 INSERT INTO ... SELECT ... GROUP BY 形态（分层 ETL 的标准形状）；
    无 GROUP BY / 非 INSERT 语句直接返回（不校验）。

    放行条件（任一满足）：
    1. 输出项含聚合函数（含 ROUND/COALESCE 包裹形态）
    2. 输出项表达式归一化后与某 GROUP BY 表达式一致（如 DATE_FORMAT 分桶列）
    3. 输出项是分组列的别名重命名（g AS a）或分组列带限定前缀（s.g AS g）

    Args:
        etl_sql: 成品 ETL SQL（INSERT INTO t(...) SELECT ... FROM ... GROUP BY ...）。
        table_name: 表名（报错定位用，通常传 short name）。

    Raises:
        ValueError: 存在既非聚合表达式也不在 GROUP BY 中的输出列。
    """
    sql = (etl_sql or "").strip().rstrip(";")
    if not sql.upper().startswith("INSERT INTO") or " GROUP BY " not in sql.upper():
        return
    sel = re.split(r"\bFROM\b", sql, maxsplit=1, flags=re.IGNORECASE)[0]
    sel = re.split(r"\bSELECT\b", sel, maxsplit=1, flags=re.IGNORECASE)[1]
    group_raw = re.split(r"\bGROUP BY\b", sql, maxsplit=1, flags=re.IGNORECASE)[1]
    group_raw = re.split(r"\bHAVING\b|\bORDER BY\b|\bLIMIT\b", group_raw,
                         maxsplit=1, flags=re.IGNORECASE)[0]
    group_parts = _split_top_commas(group_raw)
    group_norm = {_norm_expr(g) for g in group_parts}
    # 分组列的裸名（剥别名/限定前缀）：g / s.g / DATE_FORMAT(...) AS d → {g, date_format} 末段
    group_leaf = {g.strip().strip("`\"").split(".")[-1].strip("`\"").lower()
                  for g in group_parts}

    bad: list[str] = []
    for col_expr in _split_top_commas(sel):
        if AGG_FN_RE.search(col_expr):
            continue
        m_as = re.search(r"\bAS\s+[`\"]?(\w+)[`\"]?\s*$", col_expr, re.IGNORECASE)
        alias = (m_as.group(1) if m_as else "").lower()
        expr = col_expr[:m_as.start()].strip() if m_as else col_expr
        expr_norm = _norm_expr(expr)
        # 条件 2：表达式与 GROUP BY 项一致（含 s.g vs g 限定差异——剥前缀再比一次）
        if expr_norm in group_norm or _strip_qualifier(expr_norm) in group_norm:
            continue
        # 条件 3：分组列的别名重命名（g AS a：表达式叶子名在分组叶子集中）
        leaf = expr.strip().strip("`\"").split(".")[-1].strip("`\"").lower()
        if alias and leaf in group_leaf:
            continue
        if leaf in group_leaf and not m_as:
            continue
        bad.append(col_expr[:80])
    if bad:
        raise ValueError(
            f"表 {table_name} 的 etl_sql 带 GROUP BY，但以下输出列既非聚合表达式也不在 "
            f"GROUP BY 中（Doris 执行期将报 not in aggregate's output）：{bad}"
        )
