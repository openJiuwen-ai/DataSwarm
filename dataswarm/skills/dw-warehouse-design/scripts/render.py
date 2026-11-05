# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""确定性 SQL 渲染器 —— 语义规格 → DDL/ETL。

SQL 只在此处生成（模板化），LLM 不手写 SQL：
- 输出与 plan_to_tasks.py 消费的 plan JSON 完全兼容
- DDL 按目标库方言渲染：doris 固化 DUPLICATE KEY / HASH 分桶 / replication；
  mysql 生成 InnoDB 兼容 DDL（类型映射 + 索引首列）
- 列定义格式兼容 plan_to_tasks._parse_ddl_columns（`col` 开头逐行）
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import infer
from schema import Column, LAYER_ORDER, TableSpec

# _dw_common/scripts——优先从本 skill 同级目录解析（dev 与 runtime 通用）；
# 缺失时回退项目根 resources 路径（同 plan_to_tasks.py 模式）。
_DW_COMMON_DIR = Path(__file__).resolve().parent.parent.parent / "_dw_common" / "scripts"
if _DW_COMMON_DIR.is_dir():
    if str(_DW_COMMON_DIR) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON_DIR))
else:
    _DW_COMMON_FALLBACK = (
        Path(__file__).resolve().parents[7]
        / "jiuwenswarm" / "resources" / "agent" / "workspace" / "skills"
        / "_dw_common" / "scripts"
    )
    if _DW_COMMON_FALLBACK.is_dir() and str(_DW_COMMON_FALLBACK) not in sys.path:
        sys.path.insert(0, str(_DW_COMMON_FALLBACK))
from dw_type_map import dialect_type, quote_ident  # noqa: E402
from dw_aggregate_check import AGG_FN_RE as _AGG_RE  # noqa: E402  # 批次 1b G8 唯一真相源


def _q(identifier: str) -> str:
    return f"`{identifier}`"


def _q_for(dialect: str, identifier: str) -> str:
    """方言化标识符引用：duckdb 双引号，其余（doris/mysql）反引号（dw_type_map 真相源）。"""
    return quote_ident(identifier, dialect)


def _esc(text: str) -> str:
    return text.replace("'", "''")


def _buckets(row_count: int) -> int:
    if row_count < 10_000_000:
        return 4
    if row_count < 100_000_000:
        return 10
    return 32


def _is_float_dtype(dtype: str) -> bool:
    return dtype.strip().upper() in {"FLOAT", "DOUBLE"}


# Doris 类型 → MySQL 类型：唯一真相源在 _dw_common/scripts/dw_type_map.py
# （dialect-convergence 批次 1b G3；此处仅保留薄委托）。
def _mysql_type(dtype: str) -> str:
    return dialect_type(dtype, "mysql")


def _duckdb_type(dtype: str) -> str:
    """Doris → DuckDB 类型：dw_type_map 真相源（仅 LARGEINT 需映射）。"""
    return dialect_type(dtype, "duckdb")


def _key_safe_columns(columns: list[Column], reorder_for_key: bool) -> tuple[list[Column], Column]:
    """返回 Doris 可用 key 列；FLOAT/DOUBLE key 会触发 errCode = 2。"""
    eligible = next((column for column in columns if not _is_float_dtype(column.dtype)), None)
    if reorder_for_key and eligible is not None and eligible.name != columns[0].name:
        index = columns.index(eligible)
        return [eligible, *columns[:index], *columns[index + 1:]], eligible
    if _is_float_dtype(columns[0].dtype):
        decimal_key = Column(
            columns[0].name, "DECIMAL(38,12)", columns[0].nullable, columns[0].comment,
        )
        return [decimal_key, *columns[1:]], decimal_key
    return columns, columns[0]


def _render_mysql_ddl(short_name: str, columns: list[Column], source_note: str = "") -> str:
    """MySQL 兼容 DDL：无 DUPLICATE KEY/HASH 分桶，首列建索引，InnoDB + utf8mb4。"""
    if not columns:
        raise ValueError(f"表 {short_name} 没有列，无法渲染 DDL")
    lines = []
    for c in columns:
        null_sql = "NULL" if c.nullable else "NOT NULL"
        comment = c.comment or source_note
        comment_sql = f" COMMENT '{_esc(comment)}'" if comment else ""
        lines.append(f"  {_q(c.name)} {_mysql_type(c.dtype)} {null_sql}{comment_sql}")
    body = ",\n".join(lines)
    key_col = columns[0]
    if _is_float_dtype(key_col.dtype):
        key_col = next((c for c in columns if not _is_float_dtype(c.dtype)), columns[0])
    return (
        f"CREATE TABLE IF NOT EXISTS {_q(short_name)} (\n{body},\n"
        f"  KEY {_q('idx_' + short_name[:50])} ({_q(key_col.name)})\n"
        f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )


def _render_duckdb_ddl(short_name: str, columns: list[Column]) -> str:
    """DuckDB DDL：双引号标识符；无列内 COMMENT / DUPLICATE KEY / 分桶 /
    PROPERTIES（duckdb 不支持列内 COMMENT，注释留在 plan 元数据）；
    无需 Doris 的 key 列重排。"""
    if not columns:
        raise ValueError(f"表 {short_name} 没有列，无法渲染 DDL")
    lines = [
        f'  {_q_for("duckdb", c.name)} {_duckdb_type(c.dtype)} {"NULL" if c.nullable else "NOT NULL"}'
        for c in columns
    ]
    body = ",\n".join(lines)
    return f'CREATE TABLE IF NOT EXISTS {_q_for("duckdb", short_name)} (\n{body}\n)'


def render_ddl(
    short_name: str,
    columns: list[Column],
    row_count: int = 0,
    source_note: str = "",
    reorder_for_key: bool = False,
    dialect: str = "doris",
) -> str:
    """按方言渲染规范 DDL（sqlite 后端由 plan_to_tasks._to_sqlite_ddl 翻译）。"""
    if dialect == "mysql":
        return _render_mysql_ddl(short_name, columns, source_note=source_note)
    if dialect == "duckdb":
        return _render_duckdb_ddl(short_name, columns)
    if not columns:
        raise ValueError(f"表 {short_name} 没有列，无法渲染 DDL")
    ddl_columns, key_column = _key_safe_columns(columns, reorder_for_key)
    key_col = key_column.name
    lines = []
    for c in ddl_columns:
        null_sql = "NULL" if c.nullable else "NOT NULL"
        comment = c.comment or source_note
        comment_sql = f" COMMENT '{_esc(comment)}'" if comment else ""
        lines.append(f"  {_q(c.name)} {c.dtype} {null_sql}{comment_sql}")
    body = ",\n".join(lines)
    return (
        f"CREATE TABLE IF NOT EXISTS {_q(short_name)} (\n{body}\n) ENGINE=OLAP\n"
        f"DUPLICATE KEY({_q(key_col)})\n"
        f"DISTRIBUTED BY HASH({_q(key_col)}) BUCKETS {_buckets(row_count)}\n"
        f'PROPERTIES ("replication_num" = "1")'
    )


def render_ods_plan_table(biz_database: str, file_info: dict, dialect: str = "doris") -> dict:
    """CSV 推断结果 → plan 的 ODS 表条目。"""
    short = f"ods_{infer.sanitize_stem(str(file_info['stem']))}"
    columns = [
        Column(c["name"], c["dtype"], c.get("nullable", True), "CSV 源列")
        for c in file_info["columns"]
    ]
    ddl = render_ddl(short, columns, row_count=int(file_info.get("row_count") or 0),
                     source_note=f"CSV: {file_info['file']}", dialect=dialect)
    etl = ("SELECT " + ", ".join(_q_for(dialect, c["name"]) for c in file_info["columns"])
           + f" FROM {_q_for(dialect, file_info['stem'])}")
    return {
        "table_name": f"{biz_database}.{short}",
        "layer": "ODS",
        "ddl_sql": ddl,
        "etl_sql": etl,
        "source_tables": [file_info["file"]],
        "row_count": int(file_info.get("row_count") or 0),
    }


def _default_group_expr(t: TableSpec, col_name: str, dialect: str = "doris") -> str:
    first = t.source_tables[0]
    alias = t.source_aliases.get(first, first.split(".")[-1])
    return f"{alias}.{_q_for(dialect, col_name)}"


def _render_column_expr(expr: str, dtype: str, dialect: str) -> str:
    """Render a column expression with dialect-specific target typing."""
    if dialect != "duckdb" or dtype.strip().upper() not in {"DATE", "DATETIME", "TIMESTAMP"}:
        return expr
    if re.search(r"\b(?:TRY_)?CAST\s*\(", expr, re.I):
        return expr
    target_type = "DATE" if dtype.strip().upper() == "DATE" else "TIMESTAMP"
    return f"TRY_CAST(({expr}) AS {target_type})"


def _assert_aggregate_output(t: TableSpec, select_parts: list[str], dialect: str = "doris") -> None:
    """GROUP BY 查询的静态校验：非聚合列必须出现在 GROUP BY 里。

    缺失时 Doris Nereids 在执行期才报 "not in aggregate's output"（实例跑到
    该任务才失败）。设计阶段就大声失败，报出问题列名。
    聚合函数判定正则（AGG_RE）来自 _dw_common/dw_aggregate_check 唯一真相源。
    """
    if not t.group_by:
        return
    column_types = {column.name: column.dtype for column in t.columns}
    group_exprs = [
        _render_column_expr(
            t.column_exprs.get(g) or _default_group_expr(t, g, dialect),
            column_types.get(g, ""),
            dialect,
        )
        for g in t.group_by
    ]
    group_norm = {re.sub(r"\s+", "", g).lower() for g in group_exprs}
    bad: list[str] = []
    for part in select_parts:
        expr = part.rsplit(" AS ", 1)[0]
        if _AGG_RE.search(expr):
            continue
        if re.sub(r"\s+", "", expr).lower() in group_norm:
            continue
        bad.append(part)
    if bad:
        raise ValueError(
            f"表 {t.short_name} 的 ETL 存在 GROUP BY 但以下列既非聚合表达式也不在 "
            f"GROUP BY 里（Doris 执行期会报 not in aggregate's output）：{bad}；"
            f"请在语义规格中为它们声明聚合表达式或加入 group_by"
        )


def render_derived_etl(t: TableSpec, dialect: str = "doris") -> str:
    """语义规格 → INSERT INTO ... SELECT ... [JOIN] [WHERE] [GROUP BY]。"""
    def q(name: str) -> str:
        # 就近缩短多行拼接
        return _q_for(dialect, name)

    col_list = ", ".join(q(c.name) for c in t.columns)
    select_parts = []
    for c in t.columns:
        expr = t.column_exprs.get(c.name)
        if expr is None:
            expr = _default_group_expr(t, c.name, dialect)
        expr = _render_column_expr(expr, c.dtype, dialect)
        select_parts.append(f"{expr} AS {q(c.name)}")
    _assert_aggregate_output(t, select_parts, dialect)

    first = t.source_tables[0]
    first_short = first.split(".")[-1]
    first_alias = t.source_aliases.get(first, first_short)
    select_block = ",\n".join(f"  {s}" for s in select_parts)
    parts = [
        f"INSERT INTO {q(t.short_name)} ({col_list})",
        "SELECT",
        select_block,
        f"FROM {q(first_short)} AS {q(first_alias)}",
    ]
    for j in t.joins:
        right_short = j.right.split(".")[-1]
        right_alias = t.source_aliases.get(j.right, right_short)
        left_alias = t.source_aliases.get(j.left, j.left.split(".")[-1])
        on = " AND ".join(
            f"{left_alias}.{q(obj.left_col)} = {right_alias}.{q(obj.right_col)}" for obj in j.on
        )
        parts.append(f"{j.kind.upper()} JOIN {q(right_short)} AS {q(right_alias)} ON {on}")
    if t.where:
        parts.append(f"WHERE {t.where}")
    if t.group_by:
        column_types = {column.name: column.dtype for column in t.columns}
        group_exprs = [
            _render_column_expr(
                t.column_exprs.get(g) or _default_group_expr(t, g, dialect),
                column_types.get(g, ""),
                dialect,
            )
            for g in t.group_by
        ]
        parts.append("GROUP BY " + ", ".join(group_exprs))
    return "\n".join(parts)


def render_derived_plan_table(biz_database: str, t: TableSpec, dialect: str = "doris") -> dict:
    """TableSpec → plan 的派生层表条目。"""
    ddl = render_ddl(
        t.short_name, t.columns, source_note=t.comment, reorder_for_key=True,
        dialect=dialect,
    )
    return {
        "table_name": f"{biz_database}.{t.short_name}",
        "layer": t.layer,
        "ddl_sql": ddl,
        "etl_sql": render_derived_etl(t, dialect=dialect),
        "source_tables": list(t.source_tables),
    }


@dataclass(frozen=True)
class PlanSpec:
    """plan 组装入参（13 项签名收拢为单一规格对象）。"""

    plan_id: str
    name: str
    business: str
    biz_database: str
    purpose: str
    tier: str
    csv_dir: str
    files: list[str]
    ods_tables: list[dict]
    derived_tables: list[dict]
    schedule_cron: str = "0 2 * * *"
    file_options: dict[str, dict] | None = None
    dialect: str = "doris"


def build_plan(spec: PlanSpec) -> dict:
    """组装 plan JSON（与 plan_to_tasks.py 消费格式完全兼容，额外携带 lifecycle_tier）。"""
    tables = sorted(spec.ods_tables + spec.derived_tables,
                    key=lambda t: (LAYER_ORDER[t["layer"]], t["table_name"]))
    source = {"csv_dir": str(spec.csv_dir), "files": spec.files}
    if spec.file_options:
        source["file_options"] = spec.file_options
    return {
        "plan_id": spec.plan_id,
        "name": spec.name,
        "business": spec.business,
        "biz_database": spec.biz_database,
        "purpose": spec.purpose,
        "lifecycle_tier": spec.tier,
        "dialect": spec.dialect,
        "source_config": json.dumps(source, ensure_ascii=False),
        "schedule_cron": spec.schedule_cron,
        "tables": tables,
    }
