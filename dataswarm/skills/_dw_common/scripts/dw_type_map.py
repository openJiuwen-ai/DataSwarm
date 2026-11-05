#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""dw_type_map.py —— 语义类型映射与方言引号的唯一真相源（skill 侧纯函数）。

方案 docs/archive/delivered/dialect-convergence-plan.md 批次 1b（G3）：此前 Doris→MySQL 类型映射
在 dw-warehouse-design/render.py 与 dw-warehouse-build/plan_to_tasks.py 各维护
一份（后者注释自认「与 render.py 一致」），引号规则散落多处。收口到本文件：

- 语义规格统一写 Doris 风格类型（references/semantic-spec.md 契约），
  方言差异在此映射，不散落渲染器/翻译器
- 消费方：dw-warehouse-design/render.py（设计期渲染）、
  dw-warehouse-build/plan_to_tasks.py（旧 plan 兜底翻译）
- 纯函数、无连接能力、无 import jiuwenswarm（_dw_common 约定，见 dw_sql_utils.py）
"""
from __future__ import annotations

import re

# Doris 语义类型 → MySQL 类型（固定映射；参数化类型见 mysql_type）
MYSQL_TYPE_MAP = {
    "STRING": "TEXT",
    "VARCHAR(65533)": "TEXT",
    "LARGEINT": "BIGINT",
}

# Doris 语义类型 → DuckDB 类型（duckdb 原生兼容大多数，仅 LARGEINT 需映射）
DUCKDB_TYPE_MAP = {
    "LARGEINT": "BIGINT",
}


def mysql_type(dtype: str) -> str:
    """Doris 语义类型 → MySQL 类型。

    VARCHAR(n)：MySQL 行格式限制单列 ≤ 16383 字符，收敛到安全上限内。
    DECIMAL(P,S)：MySQL 上限 (65,30)，S 超限收敛。
    其余（INT/BIGINT/DATE/DATETIME/BOOLEAN 等）MySQL 兼容，原样返回。
    """
    upper = dtype.strip().upper()
    mapped = MYSQL_TYPE_MAP.get(upper)
    if mapped:
        return mapped
    m = re.match(r"^VARCHAR\((\d+)\)$", upper)
    if m:
        n = min(int(m.group(1)), 16383)
        return f"VARCHAR({n})"
    m = re.match(r"^DECIMAL\((\d+),\s*(\d+)\)$", upper)
    if m:
        p, s = int(m.group(1)), int(m.group(2))
        p, s = min(p, 65), min(s, 30, p)
        return f"DECIMAL({p},{s})"
    return upper


def duckdb_type(dtype: str) -> str:
    """Doris 语义类型 → DuckDB 类型（仅 LARGEINT 需映射，其余原生兼容）。"""
    upper = dtype.strip().upper()
    return DUCKDB_TYPE_MAP.get(upper, upper)


def dialect_type(dtype: str, dialect: str) -> str:
    """按目标方言映射语义类型；doris（语义类型本体）与未知方言原样返回。"""
    if dialect == "mysql":
        return mysql_type(dtype)
    if dialect == "duckdb":
        return duckdb_type(dtype)
    return dtype


def quote_ident(identifier: str, dialect: str) -> str:
    """方言化标识符引用：duckdb/sqlite 双引号，doris/mysql 反引号。"""
    if dialect in ("duckdb", "sqlite"):
        return f'"{identifier}"'
    return f"`{identifier}`"
