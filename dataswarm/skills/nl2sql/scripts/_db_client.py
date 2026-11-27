# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NL2SQL transport over the shared authorized business-read API."""

import json
import sys
from pathlib import Path

# 固定标准输出编码，不跟系统 locale（中文 Windows 是 cp936）走：
# 显式切到 UTF-8，避免数据/错误里的中文在子进程管道里乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# dw_client 委托层（_dw_common/scripts，方案批次 2：连接层已上收服务端）
_DW_COMMON_DIR = (Path(__file__).resolve().parent.parent.parent
                  / "_dw_common" / "scripts")
if str(_DW_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DW_COMMON_DIR))
import dw_client as db_connector  # type: ignore  # noqa: E402
from dw_error_classify import classify_error, SUGGESTIONS  # type: ignore  # noqa: E402,F401 —— 唯一真相源（方言收敛批次 2-G7）


def emit(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=True, default=str))


def failure(kind: str, why: str, *, stage: str = "", retry: bool = False) -> dict:
    """nl2sql 统一失败信封：``{"ok": false, "error": {kind, stage, why, hint, retry}}``。

    kind 沿用 dw_error_classify 的分类值；hint 取其 SUGGESTIONS（无对应项则为空串）。
    stage 为空时不输出（元数据类脚本失败无阶段归因）。
    """
    error = {"kind": kind, "why": why, "hint": SUGGESTIONS.get(kind, ""), "retry": retry}
    if stage:
        error["stage"] = stage
    return {"ok": False, "error": error}


def emit_error(kind: str, why: str) -> None:
    """失败信封写 stderr 并以退出码 1 结束（元数据类脚本的硬失败出口）。"""
    print(json.dumps(failure(kind, why), ensure_ascii=True), file=sys.stderr)
    sys.exit(1)


def db_ref() -> str:
    """读默认业务库 db-ref（多工作空间：按 workspace 解析链，argv > env > 默认）。

    nl2sql 不接 --db-ref，统一走当前工作空间业务库（env JIUWENSWARM_WORKSPACE_ID
    由 bash 工具注入）。多工作空间缺上下文时大声失败（WORKSPACE_CONTEXT_REQUIRED）。
    """
    ref = db_connector.default_db_ref(
        getattr(db_connector, "resolve_workspace_id", lambda explicit=None: "")()
    )
    if not ref:
        emit_error(
            "MISSING_CONFIG",
            "业务库未配置（工作空间/datasource.yaml default_db 均为空），"
            "参考 dw-db-connector SKILL.md 配置 db-ref",
        )
    return ref


def query(sql: str, limit: int = 10000) -> dict:
    """只读查询，委托 db_connector.query。返回 {row_count, columns, rows, ...}。

    不支持 params——dwcli 无 params 通道，SQL 须字面拼（同 dw_client.query 契约）。
    """
    return db_connector.query(db_ref(), sql, limit=limit)


def query_dict(sql: str, limit: int = 10000) -> list[dict]:
    """只读查询，返回 list[dict]（列名: 值）。不支持 params（同 query）。"""
    return db_connector.query_dict(db_ref(), sql, limit=limit)


def tables() -> dict:
    return db_connector.tables(db_ref())


def schema(table: str) -> dict:
    return db_connector.schema(db_ref(), table)


def schema_columns() -> dict:
    """Batch catalog columns through the metadata API."""
    return db_connector.schema_columns(db_ref())


def execution_context() -> dict:
    """Get the server's source capability and dialect projection."""
    return db_connector.business_read("context", db_ref())


def quote_ident(name: str) -> str:
    context = execution_context()
    opening, closing = context["identifier_quote"], context["identifier_quote_close"]
    if not opening or not closing:
        raise ValueError("source has no SQL generation dialect")
    return opening + name.replace(closing, closing * 2) + closing


def quote_table(name: str) -> str:
    """Resolve catalog identity server-side, preserving literal dots and schemas."""
    return schema(name)["sql_identifier"]


def database_name() -> str:
    return execution_context()["database"]


def source_type() -> str:
    context = execution_context()
    return context["sql_dialect"] or context["target_sql_dialect"] or ""


def duckdb_tables() -> list[str]:
    return list(tables().get("tables", []))


def preflight(sql: str, static_only: bool = False) -> dict:
    return db_connector.business_read("validate" if static_only else "preflight", db_ref(), sql=sql)


def sample_values(table: str, columns: list[str], limit: int = 20) -> dict:
    return db_connector.business_read("values", db_ref(), dataset=table, columns=columns, limit=limit)


def count_rows(table: str) -> dict:
    return db_connector.business_read("count", db_ref(), dataset=table)


# ==========================================================================
# 语义增强通道（一期接线：表召回混合化，schema 向量设计 §6 P1）
# ==========================================================================
def glossary_resolve(query_text: str, limit: int = 3) -> dict:
    """术语库三级召回（精确别名/向量/LIKE）。服务端原样返回
    ``{"matches": [...], "warnings": [...]}``；不可达时抛 RuntimeError，调用方降级。"""
    return db_connector.glossary_resolve(query_text, limit=limit)


def metric_recall(query_text: str, limit: int = 5) -> dict:
    """Return published metric candidates in the current workspace."""
    return db_connector.metric_recall(query_text, limit=limit, db_ref=db_ref())


def field_context(table_specs: list[dict]) -> dict:
    """Read published profiles for recalled fields; no fresh business sampling."""
    return db_connector.semantic_field_context(db_ref(), table_specs)


def semantic_vector_recall(query_text: str, limit: int = 20) -> dict:
    """schema 向量表/列语义召回（按当前业务库 db_ref 反查注册源）。

    服务端降级契约：通道关闭/未绑定返回空 tables + warning，不是错误。
    不可达时抛 RuntimeError，调用方降级纯词法。
    """
    return db_connector.semantic_vector_recall(db_ref(), query_text, limit=limit)


def value_matches(query_text: str, limit: int = 20) -> dict:
    """Values from the semantic value dictionary that the question mentions, with their columns.

    ``ambiguous`` is non-empty when one value exists under several columns.
    Raises RuntimeError when the service is unreachable; callers treat that as
    a missing enhancement, not a failure.
    """
    return db_connector.semantic_value_matches(db_ref(), query_text, limit=limit)
